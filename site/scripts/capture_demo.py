"""Capture the website's live-card demo from a running Home Assistant.

The website runs the real, shipped Lovelace card on a stand-in ``hass``. What
it feeds that card is a snapshot of one real car -- states, registry rows and
the service responses the card's views ask for -- because synthetic fixtures
never look like a car that has actually been driven for a month.

This repository is public, so the snapshot is scrubbed by *allowlist*, not by
rule: an attribute nobody listed here never reaches the file.

* The VIN becomes ``WBADEMO0000000001``; registry ids are replaced.
* The car's position is moved to a public place; nothing keeps the real one.
* Trips keep their numbers (distance, energy, charge, time of day) but every
  place is swapped for a fictional one around Munich, matched by how far it was
  from home, and every route is drawn fresh between those places -- a real
  route, even shifted, still has the shape of someone's commute.
* Charging sessions keep zones named ``Home``/``Work`` only.

Read the output before committing it.

Usage (host and login come from the environment, never from this repo)::

    HA_URL=http://homeassistant.local:8123 HA_USER=... HA_PASSWORD=... \
        python site/scripts/capture_demo.py
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import pathlib
import random
import re
import sys
from datetime import datetime, timezone
from typing import Any

import aiohttp

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_OUT = _ROOT / "site" / "src" / "demo" / "i5.json"
_IMAGE = _ROOT / "site" / "src" / "demo" / "i5.png"

DEMO_VIN = "WBADEMO0000000001"
DEMO_DEVICE = "demo-car"
DEMO_ENTRY = "demo-entry"
# Where the demo car is parked: BMW Welt, a public place.
PARKED = (48.1771, 11.5562)
PARKED_ALTITUDE_M = "515"  # the real altitude would narrow down where the car sleeps
HOME = (48.1642, 11.5864)

# Fictional destinations, each with its distance from HOME in km (computed
# below). A real destination is replaced by the one at the nearest distance.
PLACES: list[tuple[str, str | None, tuple[float, float]]] = [
    ("Work", "Work", (48.1767, 11.5590)),
    ("Viktualienmarkt, München", None, (48.1351, 11.5763)),
    ("Olympiapark, München", None, (48.1731, 11.5466)),
    ("Allianz Arena, München", None, (48.2188, 11.6247)),
    ("Garching, Forschungszentrum", None, (48.2650, 11.6710)),
    ("Flughafen München", None, (48.3538, 11.7861)),
    ("Starnberg, Seepromenade", None, (47.9990, 11.3440)),
    ("Freising, Domberg", None, (48.3990, 11.7450)),
    ("Tegernsee, Seestraße", None, (47.7120, 11.7580)),
    ("Augsburg, Rathausplatz", None, (48.3689, 10.8978)),
]

# Attributes that may reach the file, per entity. Anything else is dropped.
KEEP_ATTRS = {
    "descriptor",
    "cluster",
    "cluster_name",
    "category",
    "device_class",
    "unit_of_measurement",
    "state_class",
    "options",
    "icon",
    "timestamp",
    "tire_axle",
    "tire_side",
    "tire_metric",
    "fetched_at",
    "wear_status",
    "wear_status_color",
    "wear_value",
    "due_mileage_km",
    "defect_status",
    "defect_status_color",
    "quality_status",
    "season",
    "tread",
    "tread_manufacturer",
    "dimension",
    "energy_kwh",
    "partial",
    "items",
    "last_reset",
    "energy_mix",
    "solar_percent",
    "sessions",
    "duration_s",
    "soc_start",
    "soc_end",
    "peak_power_kw",
    "zone",
    "location_assumed",
    "cost_source",
    "samples",
    "samples_needed",
    "confident",
    "usable_capacity_kwh",
    "nominal_capacity_kwh",
    "vs_new_percent",
    "suspicious",
    "trend",
    "trip_count",
    "business_km",
    "private_km",
    "commute_km",
    "unclassified_km",
    "status",
    "range_full_km",
    "range_now_km",
    "soc_percent",
    "consumption_kwh_per_100km",
    "consumption_source",
    "consumption_window_days",
    "consumption_distance_km",
    "grid_consumption_kwh_per_100km",
    "measured_loss_percent",
    "capacity_kwh",
    "capacity_source",
    "bmw_range_km",
    "bmw_range_full_km",
    "vs_bmw_percent",
    "label",
    "wheels_reported",
    "resolved",
    "tracking_type",
    "in_zones",
    "source_type",
    "gps_accuracy",
    "attribution",
}
BASIC_DATA_KEEP = (
    "model_name",
    "series",
    "body_type",
    "drive_train",
    "propulsion_type",
    "charging_modes",
)


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 6371 * 2 * math.asin(math.sqrt(h))


async def _token(session: aiohttp.ClientSession, url: str, user: str, password: str) -> str:
    client = f"{url}/"
    async with session.post(
        f"{url}/auth/login_flow",
        json={"client_id": client, "handler": ["homeassistant", None], "redirect_uri": client},
    ) as r:
        flow = (await r.json())["flow_id"]
    async with session.post(
        f"{url}/auth/login_flow/{flow}",
        json={"client_id": client, "username": user, "password": password},
    ) as r:
        code = (await r.json())["result"]
    async with session.post(
        f"{url}/auth/token",
        data={"grant_type": "authorization_code", "code": code, "client_id": client},
    ) as r:
        return (await r.json())["access_token"]


async def _fetch(url: str, user: str, password: str) -> dict[str, Any]:
    async with aiohttp.ClientSession() as session:
        token = await _token(session, url, user, password)
        auth = {"Authorization": f"Bearer {token}"}
        async with session.get(f"{url}/api/states", headers=auth) as r:
            states = await r.json()
        async with session.ws_connect(url.replace("http", "ws", 1) + "/api/websocket") as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": token})
            await ws.receive_json()
            registry = {}
            for i, kind in enumerate(("entity", "device"), 1):
                await ws.send_json({"id": i, "type": f"config/{kind}_registry/list"})
                registry[kind] = (await ws.receive_json())["result"]
        services = {}
        for name in ("get_charging_sessions", "get_trips", "get_efficiency", "get_driving_summary"):
            async with session.post(
                f"{url}/api/services/bavariandata/{name}?return_response", headers=auth, json={}
            ) as r:
                services[name] = (await r.json())["service_response"]
        image = None
        for st in states:
            if (
                st["entity_id"].startswith("image.")
                and st["attributes"].get("descriptor") == "vehicle_image"
            ):
                async with session.get(url + st["attributes"]["entity_picture"], headers=auth) as r:
                    image = await r.read()
        return {"states": states, "registry": registry, "services": services, "image": image}


def _scrub_text(value: Any, vin: str) -> Any:
    """Replace the VIN wherever it hides inside a structure."""

    if isinstance(value, str):
        return value.replace(vin, DEMO_VIN)
    if isinstance(value, list):
        return [_scrub_text(v, vin) for v in value]
    if isinstance(value, dict):
        return {k: _scrub_text(v, vin) for k, v in value.items()}
    return value


class Places:
    """Maps a real place to a fictional one at a similar distance from home."""

    def __init__(self, real_home: tuple[float, float]) -> None:
        self.real_home = real_home
        self.by_label: dict[str, tuple[str, str | None, tuple[float, float]]] = {}
        self.pool = [(p, _km(HOME, p[2])) for p in PLACES]

    def swap(
        self, place: dict | None, point: tuple[float, float] | None
    ) -> tuple[dict, tuple[float, float]]:
        place = place or {}
        if place.get("zone") == "Home":
            return {"zone": "Home", "address": None, "label": "Home"}, HOME
        key = place.get("label") or place.get("address") or place.get("zone") or repr(point)
        if key not in self.by_label:
            if place.get("zone"):
                self.by_label[key] = PLACES[0]
            else:
                dist = _km(self.real_home, point) if point else 5.0
                self.by_label[key] = min(self.pool[1:], key=lambda p: abs(p[1] - dist))[0]
        label, zone, coords = self.by_label[key]
        return {"zone": zone, "address": None if zone else label, "label": label}, coords

    def label(self, real: str) -> str:
        if real == "Home":
            return "Home"
        return self.by_label.get(real, PLACES[1])[0]


def _route(
    a: tuple[float, float], b: tuple[float, float], seconds: float, rng: random.Random
) -> list:
    """A plausible, entirely made-up route: a gentle bend between two points."""

    n = max(6, min(40, int(_km(a, b) * 1.5)))
    bend = rng.uniform(-0.18, 0.18)
    out = []
    for i in range(n + 1):
        f = i / n
        lat = a[0] + (b[0] - a[0]) * f
        lon = a[1] + (b[1] - a[1]) * f
        offset = math.sin(math.pi * f) * bend
        lat += -(b[1] - a[1]) * offset + rng.uniform(-0.0006, 0.0006) * (0 < i < n)
        lon += (b[0] - a[0]) * offset + rng.uniform(-0.0006, 0.0006) * (0 < i < n)
        out.append([round(lat, 5), round(lon, 5), round(seconds * f)])
    return out


def build(raw: dict[str, Any]) -> dict[str, Any]:
    entities = [e for e in raw["registry"]["entity"] if e["platform"] == "bavariandata"]
    devices = {
        d["id"]: d
        for d in raw["registry"]["device"]
        if any(i[0] == "bavariandata" and len(i[1]) == 17 for i in d["identifiers"])
    }
    if len(devices) != 1:
        sys.exit(f"expected exactly one car, found {len(devices)}")
    device = next(iter(devices.values()))
    vin = next(i[1] for i in device["identifiers"] if i[0] == "bavariandata")
    states = {s["entity_id"]: s for s in raw["states"]}
    captured = max(s["last_updated"] for s in raw["states"])

    real_home = None
    out_entities, out_states, used = {}, {}, set()
    for ent in entities:
        if ent["device_id"] != device["id"] or ent["disabled_by"] or ent["entity_id"] not in states:
            continue
        if (ent["translation_key"] or "").endswith("_testing"):
            continue  # a maintainer's experiment, not something users get
        st = states[ent["entity_id"]]
        domain = ent["entity_id"].split(".")[0]
        key = ent["translation_key"] or "entity"
        eid = f"{domain}.i5_{key}"
        n = 2
        while eid in used:
            eid, n = f"{domain}.i5_{key}_{n}", n + 1
        used.add(eid)
        attrs = {k: v for k, v in st["attributes"].items() if k in KEEP_ATTRS}
        basic = st["attributes"].get("vehicle_basic_data")
        if basic:
            attrs["vehicle_basic_data"] = {k: basic[k] for k in BASIC_DATA_KEEP if k in basic}
        if domain == "device_tracker":
            real_home = (st["attributes"]["latitude"], st["attributes"]["longitude"])
            attrs.update(latitude=PARKED[0], longitude=PARKED[1])
        if domain == "image":
            attrs["entity_picture"] = "demo/i5.png"
        if "zone" in attrs and attrs["zone"] not in ("Home", None):
            attrs["zone"] = "Work"
        if "tread_manufacturer" in attrs:
            attrs.pop("tread_manufacturer")
        state = st["state"]
        if attrs.get("descriptor", "").endswith("currentLocation.altitude"):
            state = PARKED_ALTITUDE_M
        out_states[eid] = {
            "state": state,
            "attributes": _scrub_text(attrs, vin),
            "last_changed": st["last_changed"],
            "last_updated": st["last_updated"],
        }
        display = ((ent.get("options") or {}).get("sensor") or {}).get(
            "suggested_display_precision"
        )
        out_entities[eid] = {
            "entity_id": eid,
            "device_id": DEMO_DEVICE,
            "platform": "bavariandata",
            "translation_key": ent["translation_key"],
            "entity_category": ent["entity_category"],
            **({"display_precision": display} if display is not None else {}),
        }

    rng = random.Random(7)
    places = Places(real_home or HOME)
    services = _scrub_text(raw["services"], vin)
    for trip in services["get_trips"]["trips"] + services["get_trips"].get("open_trips", []):
        track = trip.get("track") or []
        first = tuple(track[0][:2]) if track else None
        last = tuple(track[-1][:2]) if track else None
        trip["start_place"], a = places.swap(trip.get("start_place"), first)
        trip["end_place"], b = places.swap(trip.get("end_place"), last)
        seconds = track[-1][2] if track else 600
        trip["track"] = _route(a, b, seconds, rng) if track else []
    for sess in services["get_charging_sessions"]["sessions"]:
        zone = (sess.get("location") or {}).get("zone")
        sess["location"] = {"zone": zone if zone in ("Home", None) else "Work"}
    summary = services["get_driving_summary"]["summary"]
    for which in ("best_trip", "worst_trip"):
        if summary.get(which):
            summary[which]["label"] = places.label(summary[which]["label"])
    merged: dict[str, int] = {}
    for dest in summary.get("top_destinations", []):
        label = places.label(dest["label"])
        merged[label] = merged.get(label, 0) + dest["count"]
    summary["top_destinations"] = [
        {"label": k, "count": v} for k, v in sorted(merged.items(), key=lambda kv: -kv[1])
    ]

    out = {
        "_comment": "Generated by site/scripts/capture_demo.py -- scrubbed by allowlist; read before committing.",
        "captured": captured,
        "device": {
            "id": DEMO_DEVICE,
            "name": device["name"],
            "model": device["model"],
            "manufacturer": "BMW",
            "identifiers": [["bavariandata", DEMO_VIN]],
            "config_entry_id": DEMO_ENTRY,
            "primary_config_entry": DEMO_ENTRY,
            "config_entries": [DEMO_ENTRY],
        },
        "entities": out_entities,
        "states": out_states,
        "services": services,
    }
    text = json.dumps(out, ensure_ascii=False)
    for secret in (vin, device["id"], device["primary_config_entry"]):
        if secret and secret in text:
            sys.exit(f"scrub failed: {secret[:4]}… is still in the output")
    if real_home:
        # The pair, not either half: a made-up route can cross the same latitude.
        pair = rf"{real_home[0]:.3f}\d*,\s*{real_home[1]:.3f}".replace(".", r"\.", 2)
        if re.search(pair, text) or any(str(c) in text for c in real_home):
            sys.exit("scrub failed: the real home position is still in the output")
    return out


def main() -> None:
    url, user, password = (os.environ.get(k) for k in ("HA_URL", "HA_USER", "HA_PASSWORD"))
    if not (url and user and password):
        sys.exit("set HA_URL, HA_USER and HA_PASSWORD")
    raw = asyncio.run(_fetch(url.rstrip("/"), user, password))
    out = build(raw)
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if raw["image"]:
        _IMAGE.parent.mkdir(parents=True, exist_ok=True)
        _IMAGE.write_bytes(raw["image"])
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(
        f"{stamp}: {len(out['states'])} entities, "
        f"{len(out['services']['get_trips']['trips'])} trips, "
        f"{len(out['services']['get_charging_sessions']['sessions'])} sessions -> {_OUT.relative_to(_ROOT)}"
    )


if __name__ == "__main__":
    main()
