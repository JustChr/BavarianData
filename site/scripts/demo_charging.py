"""Rebuild the website demo's charging history from the demo's own trips.

The live capture's charging history is the maintainer's real one, and it
carries every stage of the integration's development: sessions imported before
there was a power curve, months before the energy mix existed, restart
fragments, a dozen solar trickles a day. As a demo it reads as noise, and the
numbers disagree with each other -- a trip leaves at 47 %, the next charge
starts at 38 %.

So the history is rebuilt, deterministically, around the trips that were
captured: one state-of-charge chain runs through every trip and every charge,
the odometer counts back from the car's current reading, and the sensors and
efficiency figures that summarise charging are recomputed from the result. The
car charges the way a PV household with a wallbox charges -- fewer, larger
sessions, on surplus sun at the weekend and on the cheap night tariff during
the week -- plus a few high-power (DC) stops on longer days out.

``capture_demo.py`` calls :func:`rebuild` on every capture; run this file on
its own to rebuild the committed ``i5.json`` in place.
"""

from __future__ import annotations

import json
import math
import pathlib
import random
from datetime import datetime, timedelta, timezone
from typing import Any

_OUT = pathlib.Path(__file__).resolve().parents[1] / "src" / "demo" / "i5.json"

CAPACITY_KWH = 76.0
LOCAL = timezone(timedelta(hours=2))  # Munich summer time; the history is Jul-Sep
HISTORY_START = datetime(2026, 7, 1, tzinfo=LOCAL)
KWH_PER_KM = 0.2  # for the short hops BMW reports no energy for
AC_EFFICIENCY = 0.92  # battery-side / grid-side for the wallbox
DC_EFFICIENCY = 0.96
TARGET_SOC = 80.0

PRICE_PV = 0.08  # feed-in forgone
PRICE_BATTERY = 0.08
PRICE_GRID_DAY = 0.32
PRICE_GRID_NIGHT = 0.21
PRICE_DC = 0.59

# (local date, place the car is parked at) -> high-power charger there, kW cap.
DC_STOPS: dict[tuple[str, str], tuple[str, float]] = {
    ("2026-07-11", "Tegernsee, Seestraße"): ("HPC · Tegernsee, Seestraße", 150.0),
    ("2026-08-08", "Starnberg, Seepromenade"): ("HPC · Starnberg, Seepromenade", 300.0),
    ("2026-08-14", "Garching, Forschungszentrum"): ("HPC · Garching, Forschungszentrum", 150.0),
    ("2026-09-06", "Augsburg, Rathausplatz"): ("HPC · Augsburg, Rathausplatz", 300.0),
}

# The i5's DC curve: kW the car accepts at a given SoC (the charger may cap it).
_DC_CURVE = [
    (0, 190),
    (10, 205),
    (30, 205),
    (40, 165),
    (50, 130),
    (60, 100),
    (70, 78),
    (80, 58),
    (90, 30),
]


def _dc_accept(soc: float) -> float:
    for (s0, p0), (s1, p1) in zip(_DC_CURVE, _DC_CURVE[1:], strict=False):
        if soc <= s1:
            return p0 + (p1 - p0) * (soc - s0) / (s1 - s0)
    return _DC_CURVE[-1][1]


def _wallbox_step(surplus_kw: float) -> float:
    """What a three-phase wallbox would set for this much surplus: 6-16 A.

    Below 6 A (4.1 kW) it cannot go, so a passing cloud is bridged by the house
    battery and then the grid -- which is why a sunny charge is rarely 100 % solar.
    """
    steps = [round(0.69 * a, 2) for a in range(6, 17)]
    usable = [s for s in steps if s <= surplus_kw]
    return max(usable) if usable else steps[0]


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value)


class _Drive:
    def __init__(
        self,
        start: datetime,
        end: datetime,
        km: float,
        kwh: float,
        home: bool,
        place: str | None,
        trip=None,
    ):
        self.start, self.end, self.km, self.kwh, self.home, self.place, self.trip = (
            start,
            end,
            km,
            kwh,
            home,
            place,
            trip,
        )


def _phantom_drives(until: datetime, rng: random.Random) -> list[_Drive]:
    """The weeks before trip recording was switched on: a commute and weekends."""
    drives: list[_Drive] = []
    day = HISTORY_START
    while day < until:
        wd = day.weekday()
        at = lambda h, m, d=day: d.replace(hour=h, minute=0) + timedelta(minutes=m)  # noqa: E731
        if day.date().isoformat() == "2026-07-11":
            drives.append(_Drive(at(9, 5), at(10, 10), 62.0, 12.6, False, "Tegernsee, Seestraße"))
            drives.append(_Drive(at(16, 40), at(17, 50), 63.0, 12.1, True, "Home"))
        elif wd < 5:
            go, back = rng.randint(0, 25), rng.randint(0, 50)
            drives.append(
                _Drive(
                    at(6, 5 + go),
                    at(6, 40 + go),
                    34.0,
                    round(rng.uniform(5.4, 7.2), 2),
                    False,
                    "Work",
                )
            )
            drives.append(
                _Drive(
                    at(15, 50) + timedelta(minutes=back),
                    at(16, 30) + timedelta(minutes=back),
                    34.0,
                    round(rng.uniform(6.0, 7.6), 2),
                    True,
                    "Home",
                )
            )
        elif wd == 5:
            drives.append(
                _Drive(at(10, 20), at(10, 45), 14.0, 2.9, False, "Viktualienmarkt, München")
            )
            drives.append(_Drive(at(12, 30), at(12, 55), 14.0, 2.7, True, "Home"))
        day += timedelta(days=1)
    return [d for d in drives if d.end < until]


class _Sim:
    def __init__(self, drives: list[_Drive], rng_seed: int, overrides: dict[int, float]):
        self.drives = drives
        self.rng = random.Random(rng_seed)
        self.overrides = overrides
        self.sessions: list[dict[str, Any]] = []
        self.soc = 62.0
        self.sun: dict[str, float] = {
            "2026-09-12": 0.9,
            "2026-09-20": 0.85,
            "2026-09-26": 0.95,
        }  # a sunny September
        self.dc_starts = [
            d.end
            for d in drives
            if (d.end.astimezone(LOCAL).date().isoformat(), d.place) in DC_STOPS
        ]

    def sunny(self, day: str) -> float:
        if day not in self.sun:
            month = int(day[5:7])
            clear = {7: 0.7, 8: 0.65, 9: 0.55}.get(month, 0.5)
            self.sun[day] = (
                self.rng.uniform(0.8, 1.0)
                if self.rng.random() < clear
                else self.rng.uniform(0.3, 0.55)
            )
        return self.sun[day]

    # -- charging ----------------------------------------------------------
    def _target(self, default: float) -> float:
        return self.overrides.get(len(self.sessions), default)

    def _session(
        self, start, end, soc0, curve, grid_kwh, energy_kwh, mix, cost, location, reason, enriched
    ):
        self.sessions.append(
            {
                "start": start,
                "end": end,
                "soc_start": soc0,
                "soc_end": self.soc,
                "energy_kwh": round(energy_kwh, 3),
                "grid_kwh": round(grid_kwh, 3),
                "peak_power_kw": round(max(p for _, p in curve), 2),
                "power_curve": curve,
                "location": location,
                "cost": cost,
                "end_reason": reason,
                "enriched": enriched,
                "energy_mix": mix,
            }
        )

    def ac(
        self, start: datetime, stop_by: datetime, solar: bool, target: float | None = None
    ) -> None:
        target = self._target(target or (90.0 if solar else TARGET_SOC))
        if self.soc >= target - 3:
            return
        soc0 = self.soc
        t, step = start, 120
        curve: list[list[float]] = []
        mix = {"pv": 0.0, "battery": 0.0, "grid": 0.0}
        cost = 0.0
        sun = self.sunny(start.astimezone(LOCAL).date().isoformat())
        home_battery = self.rng.uniform(3.0, 6.0)  # kWh the house battery can spare
        reason = "CHARGINGENDED"
        while True:
            local = t.astimezone(LOCAL)
            hour = local.hour + local.minute / 60
            if solar:
                clear = 9.8 * sun * max(0.0, math.cos((hour - 13.4) / 6.4 * math.pi / 2)) ** 1.3
                pv = clear
                pv *= 1 - (self.rng.random() * 0.35 if sun < 0.6 else self.rng.random() * 0.06)
                if self.rng.random() < 0.08:  # a passing cloud
                    pv *= 0.35
                surplus = pv - 0.55
                # The anchored last charge runs on in min+PV mode to reach its SoC.
                if clear < 3.5 and hour > 15.0 and len(self.sessions) not in self.overrides:
                    reason = "NOCHARGING"
                    break
                kw = _wallbox_step(max(surplus, 4.14))
                from_pv = min(kw, max(surplus, 0.0))
                short = kw - from_pv
                from_bat = min(short, home_battery / (step / 3600))
                home_battery -= from_bat * step / 3600
                parts = {"pv": from_pv, "battery": from_bat, "grid": short - from_bat}
            else:
                kw = 10.9 + self.rng.uniform(-0.08, 0.06)
                parts = {"pv": 0.0, "battery": 0.0, "grid": kw}
            if self.soc >= 78:  # the pack tapers the last few percent on AC too
                kw *= max(0.55, 1 - (self.soc - 78) * 0.06)
                parts = (
                    {k: v * kw / sum(parts.values()) for k, v in parts.items()}
                    if sum(parts.values())
                    else parts
                )
            curve.append([int((t - start).total_seconds()), round(kw, 2)])
            hours = step / 3600
            for k, v in parts.items():
                mix[k] += v * hours
            cost += hours * (
                parts["pv"] * PRICE_PV
                + parts["battery"] * PRICE_BATTERY
                + parts["grid"] * (PRICE_GRID_DAY if solar or 6 <= hour < 22 else PRICE_GRID_NIGHT)
            )
            self.soc += kw * hours * AC_EFFICIENCY / CAPACITY_KWH * 100
            t += timedelta(seconds=step)
            if self.soc >= target:
                self.soc = target
                break
            if t >= stop_by:
                reason = "NOCHARGING"
                break
        curve.append([int((t - start).total_seconds()), curve[-1][1]])
        grid_kwh = sum(mix.values())
        if grid_kwh < 3:  # not worth a row: undo
            self.soc = soc0
            return
        mix = {k: round(v, 3) for k, v in mix.items() if v >= 0.05}
        mix["solar_percent"] = round(100 * mix.get("pv", 0.0) / grid_kwh, 1)
        self._session(
            start,
            t,
            soc0,
            curve,
            grid_kwh,
            grid_kwh * AC_EFFICIENCY,
            mix,
            {"amount": round(cost, 2), "currency": "EUR", "source": "tariff"},
            {"zone": "Home"},
            reason,
            False,
        )

    def dc(self, start: datetime, stop_by: datetime, label: str, cap: float) -> None:
        soc0 = self.soc
        target = self._target(TARGET_SOC)
        t, step = start, 20
        curve: list[list[float]] = []
        energy = 0.0
        while self.soc < target and t < stop_by:
            secs = (t - start).total_seconds()
            ramp = min(1.0, 0.25 + secs / 80)  # handshake and ramp-up
            kw = min(cap * 0.97, _dc_accept(self.soc)) * ramp * self.rng.uniform(0.985, 1.0)
            if not curve or secs - curve[-1][0] >= 60:
                curve.append([int(secs), round(kw, 1)])
            energy += kw * step / 3600
            self.soc = min(target, self.soc + kw * step / 3600 / CAPACITY_KWH * 100)
            t += timedelta(seconds=step)
        curve.append([int((t - start).total_seconds()), curve[-1][1]])
        grid = energy / DC_EFFICIENCY
        self._session(
            start,
            t,
            soc0,
            curve,
            grid,
            energy,
            None,
            {"amount": round(grid * PRICE_DC, 2), "currency": "EUR", "source": "bmw"},
            {"zone": None, "address": label},
            "CHARGINGENDED" if self.soc >= target else "NOCHARGING",
            True,
        )

    # -- the chain ---------------------------------------------------------
    def run(self) -> None:
        for i, drive in enumerate(self.drives):
            if drive.trip is not None:
                drive.trip["soc_start"] = float(round(self.soc))
            self.soc -= drive.kwh / CAPACITY_KWH * 100
            if drive.trip is not None:
                drive.trip["soc_end"] = float(round(self.soc))
            nxt = self.drives[i + 1] if i + 1 < len(self.drives) else None
            if nxt is None:
                return
            park, leave = drive.end, nxt.start
            if self.overrides and len(self.sessions) > max(self.overrides):
                continue  # the anchored last charge has happened; nothing after it
            key = (drive.end.astimezone(LOCAL).date().isoformat(), drive.place)
            if key in DC_STOPS:
                label, cap = DC_STOPS[key]
                if not any(s["location"].get("address") == label for s in self.sessions):
                    self.dc(park + timedelta(minutes=4), leave - timedelta(minutes=3), label, cap)
                continue
            if drive.home:
                self.plan_home(park, leave)

    def plan_home(self, park: datetime, leave: datetime) -> None:
        dc_next = next(
            (s for s in self.dc_starts if timedelta(0) < s - park < timedelta(hours=72)), None
        )
        dc_soon = dc_next is not None
        if dc_soon:
            # A fast charge is coming: arrive at it low -- but not on fumes.
            needed = (
                sum(d.kwh for d in self.drives if park <= d.start < dc_next) / CAPACITY_KWH * 100
            )
            if self.soc - needed >= 18:
                return
        recent = self.sessions and park - self.sessions[-1]["end"] < timedelta(hours=16)
        local_park = park.astimezone(LOCAL)
        # Surplus sun: a weekend (or a long day at home) that spans midday.
        day = local_park.date().isoformat()
        sol_from = max(park + timedelta(minutes=6), local_park.replace(hour=10, minute=30))
        sol_to = min(leave - timedelta(minutes=10), local_park.replace(hour=17, minute=45))
        weekend = local_park.weekday() >= 5
        if (
            not dc_soon
            and not recent
            and sol_to - sol_from >= timedelta(hours=3)
            and self.sunny(day) > 0.75
            and self.soc < (70 if weekend else 50)
        ):
            self.ac(sol_from, sol_to, solar=True)
            return
        if self.soc >= 32 and not dc_soon:
            return  # the weekend sun will do it
        # Price-optimised: the cheap hours after midnight, if the car is home then.
        night = local_park.replace(hour=0, minute=40) + timedelta(minutes=self.rng.randint(0, 90))
        if local_park.hour >= 1:
            night += timedelta(days=1)
        target = round(needed + 25) if dc_soon else None
        if park < night and leave - night > timedelta(hours=3):
            self.ac(night, leave - timedelta(minutes=15), solar=False, target=target)
        elif self.soc < 25 and leave - park > timedelta(hours=1):
            self.ac(
                park + timedelta(minutes=5),
                leave - timedelta(minutes=10),
                solar=False,
                target=target,
            )


def _simulate(drives: list[_Drive], overrides: dict[int, float]) -> _Sim:
    sim = _Sim(drives, 11, overrides)
    sim.run()
    return sim


def rebuild(out: dict[str, Any]) -> dict[str, Any]:
    """Replace ``out``'s charging history, trip SoCs and charging summaries."""
    services, states = out["services"], out["states"]
    trips = sorted(services["get_trips"]["trips"], key=lambda t: t["start"])
    real = [
        _Drive(
            _parse(t["start"]),
            _parse(t["end"]),
            t["distance_km"] or 0.0,
            t["energy_kwh"]
            if t.get("energy_kwh")
            else max(0.1, (t["distance_km"] or 0) * KWH_PER_KM),
            (t.get("end_place") or {}).get("label") == "Home",
            (t.get("end_place") or {}).get("label"),
            t,
        )
        for t in trips
    ]
    drives = _phantom_drives(real[0].start, random.Random(3)) + real
    final_soc = float(states["sensor.i5_soc_estimate"]["state"])

    sim = _simulate(drives, {})
    # Land exactly on the car's current SoC: the last charge stops where the
    # trips after it leave the battery at today's reading.
    last = sim.sessions[-1]
    after = sum(d.kwh for d in drives if d.start >= last["end"])
    sim = _simulate(
        drives, {len(sim.sessions) - 1: round(final_soc + after / CAPACITY_KWH * 100, 1)}
    )
    assert round(sim.soc) == round(final_soc), (sim.soc, final_soc)

    # The odometer, counted back from today's reading.
    odo_now = float(states["sensor.i5_vehicle_vehicle_travelleddistance"]["state"])
    vin = trips[0]["vin"]
    sessions = []
    for s in sim.sessions:
        mileage = odo_now - sum(d.km for d in drives if d.start >= s["end"])
        sessions.append(
            {
                "vin": vin,
                "start": _iso(s["start"]),
                "end": _iso(s["end"]),
                "soc_start": float(round(s["soc_start"])),
                "soc_end": float(round(s["soc_end"])),
                "target_soc": TARGET_SOC,
                "energy_kwh": s["energy_kwh"],
                "grid_kwh": s["grid_kwh"],
                "peak_power_kw": s["peak_power_kw"],
                "power_curve": s["power_curve"],
                "location": s["location"],
                "location_assumed": False,
                "cost": s["cost"],
                "end_reason": s["end_reason"],
                "mileage_km": float(round(mileage)),
                "enriched": s["enriched"],
                "late_start": False,
                "interrupted": False,
                "energy_mix": s["energy_mix"],
            }
        )
    sessions.reverse()  # newest first, as the service returns them
    services["get_charging_sessions"]["sessions"] = sessions

    captured = _parse(out["captured"])
    _summaries(out, sessions, drives, captured, final_soc)
    return out


def _add_mix(total: dict[str, float], mix: dict[str, Any] | None) -> None:
    for k in ("pv", "battery", "grid", "unknown"):
        if mix and mix.get(k):
            total[k] = total.get(k, 0.0) + mix[k]


def _mix_out(total: dict[str, float]) -> dict[str, Any]:
    mix = {k: round(v, 3) for k, v in total.items() if v}
    grid_side = sum(total.values())
    mix["solar_percent"] = round(100 * total.get("pv", 0.0) / grid_side, 1) if grid_side else None
    return mix


def _summaries(out, sessions, drives, captured: datetime, soc_now: float) -> None:
    states, services = out["states"], out["services"]
    ordered = list(reversed(sessions))
    newest = sessions[0]

    # Month and session sensors.
    month = captured.astimezone(LOCAL).strftime("%Y-%m")
    in_month = [
        s for s in ordered if _parse(s["start"]).astimezone(LOCAL).strftime("%Y-%m") == month
    ]
    month_kwh = round(sum(s["grid_kwh"] for s in in_month), 3)
    month_cost = round(sum(s["cost"]["amount"] for s in in_month), 2)
    month_mix: dict[str, float] = {}
    for s in in_month:
        _add_mix(month_mix, s["energy_mix"])
    mix = _mix_out(month_mix)
    st = states["sensor.i5_charging_energy_month"]
    st["state"] = str(month_kwh)
    st["attributes"].update(energy_mix=mix, solar_percent=mix["solar_percent"])
    st = states["sensor.i5_charging_cost_month"]
    st["state"] = str(month_cost)
    st["attributes"].update(sessions=len(in_month), energy_kwh=month_kwh, partial=False)
    duration = int((_parse(newest["end"]) - _parse(newest["start"])).total_seconds())
    st = states["sensor.i5_charging_cost_session"]
    st["state"] = str(newest["cost"]["amount"])
    st["attributes"].update(
        energy_kwh=newest["grid_kwh"],
        duration_s=duration,
        soc_start=newest["soc_start"],
        soc_end=newest["soc_end"],
        peak_power_kw=newest["peak_power_kw"],
        zone=newest["location"].get("zone"),
        location_assumed=False,
        cost_source=newest["cost"]["source"],
        partial=False,
    )
    st = states["sensor.i5_charged_energy_session"]
    st["state"] = str(newest["energy_kwh"])
    st["attributes"]["last_reset"] = newest["start"]
    states["sensor.i5_charged_energy_total"]["state"] = str(
        round(sum(s["energy_kwh"] for s in ordered), 3)
    )
    states["sensor.i5_vehicle_powertrain_electric_battery_stateofcharge_target"]["state"] = str(
        int(TARGET_SOC)
    )

    # Measured consumption over the last 30 days, between two charges.
    window = [s for s in ordered if _parse(s["end"]) >= captured - timedelta(days=30)]
    first, last = window[0], window[-1]
    t0, t1 = _parse(first["end"]), _parse(last["end"])
    charged = sum(s["energy_kwh"] for s in window[1:])
    delta = (last["soc_end"] - first["soc_end"]) / 100 * CAPACITY_KWH
    km = sum(d.km for d in drives if t0 <= d.start < t1)
    used = charged - delta
    per100 = round(used / km * 100, 1)
    cost = sum(s["cost"]["amount"] for s in window[1:])
    win_mix: dict[str, float] = {}
    for s in window[1:]:
        _add_mix(win_mix, s["energy_mix"])
    eff = services["get_efficiency"]["efficiency"]
    eff["consumption"].update(
        kwh_per_100km=per100,
        used_kwh=round(used, 1),
        charged_kwh=round(charged, 1),
        battery_delta_kwh=round(delta, 1),
        distance_km=round(km),
        soc_start=first["soc_end"],
        soc_end=last["soc_end"],
        **{"from": first["end"], "to": last["end"]},
    )
    full = round(CAPACITY_KWH / per100 * 100)
    now = round(CAPACITY_KWH * soc_now / 100 / per100 * 100)
    bmw_now = eff["range"]["bmw_km"]
    eff["range"].update(
        full_km=float(full),
        kwh_per_100km=per100,
        now_km=float(now),
        soc_percent=soc_now,
        vs_bmw_percent=round((now - bmw_now) / bmw_now * 100, 1),
    )
    trend = []
    for row in eff.get("trend", []):
        ms = [
            d
            for d in drives
            if d.trip is not None and d.start.astimezone(LOCAL).strftime("%Y-%m") == row["month"]
        ]
        dist = sum(d.km for d in ms)
        if dist:
            trend.append(
                {
                    **row,
                    "kwh_per_100km": round(sum(d.kwh for d in ms) / dist * 100, 1),
                    "distance_km": round(dist),
                }
            )
    eff["trend"] = trend
    eff["cost_per_100km"] = round(cost / km * 100, 2)
    eff["energy_mix"] = _mix_out(win_mix)
    rr = states["sensor.i5_real_range"]
    rr["state"] = str(float(now))
    rr["attributes"].update(
        range_full_km=float(full),
        range_now_km=float(now),
        soc_percent=soc_now,
        consumption_kwh_per_100km=per100,
        consumption_distance_km=round(km),
        vs_bmw_percent=eff["range"]["vs_bmw_percent"],
    )


def main() -> None:
    out = json.loads(_OUT.read_text(encoding="utf-8"))
    rebuild(out)
    _OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    sessions = out["services"]["get_charging_sessions"]["sessions"]
    print(f"{len(sessions)} sessions -> {_OUT.name}")


if __name__ == "__main__":
    main()
