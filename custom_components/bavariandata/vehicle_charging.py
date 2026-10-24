"""Charging: the estimate between BMW's readings, the energy, and the ledger.

BMW sends the state of charge a whole percent at a time, sometimes an hour
apart, and its charging power in bursts; a car may charge for hours sending
neither. So the estimate is advanced from the charging power -- or, at home,
from the wallbox meter, which is far better (``soc_tracking``) -- and the
energy is integrated from the same power, bounded by what the pack can have
absorbed. A charge becomes a session record when it starts, survives a
restart as a snapshot, rides out status flaps and plugged-in pauses on a
debounce, and is closed with its cost and energy mix billed at the meter
where there is one (``history/``).

The rules that need no Home Assistant live in ``soc_tracking`` and
``history/``; this applies them to one car's state (``car_state.ChargeState``
and ``SessionState``) through its host, the coordinator. Bookkeeping must
never break the stream: every entry point swallows and logs.
"""

from __future__ import annotations

import logging
from contextlib import suppress
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Protocol

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .car_signals import (
    CHARGE_CLOSE_DEBOUNCE_S,
    CHARGE_END_REASON_DESCRIPTORS,
    CHARGE_PLUGGED_HOLD_S,
    CHARGE_PROGRESS_DESCRIPTORS,
    CHARGE_TERMINAL_STATUSES,
    OPEN_SESSION_SAVE_INTERVAL_S,
    RESTORED_SESSION_GRACE_S,
)
from .car_state import DescriptorState, Vehicle
from .const import (
    DOMAIN,
    EVENT_CHARGING_COMPLETE,
    EVENT_CHARGING_INTERRUPTED,
    EVENT_CHARGING_STARTED,
    EVENT_CHARGING_STOPPED,
)
from .debug import debug_enabled, mask_vin
from .evcc import PLUG_DESCRIPTORS, plug_state
from .history.energy_mix import SOURCE_PV, MixAccumulator, supply_shares
from .history.held_energy import HeldEnergy
from .history.meter_rate import MeterRate
from .history.pricing import (
    MODE_FIXED,
    CostAccumulator,
    PricingConfig,
    billable_energy,
    resolve_cost,
)
from .history.sessions import (
    SessionBuilder,
    energy_ceiling_kwh,
    house_supplies_this_session,
    meter_counts_this_session,
    meter_energy_bound_kwh,
    open_session_is_resumable,
    soc_is_from_session,
)
from .history.stations import CHARGE_METHOD_DESCRIPTORS, streamed_charge_method
from .soc_tracking import SocTracking
from .vehicle_triggers import charging_interrupted

_LOGGER = logging.getLogger("custom_components.bavariandata.coordinator")


def _snapshot_float(value: Any) -> Optional[float]:
    """A number out of a stored snapshot, or ``None`` if it is not one.

    Snapshots are JSON written by an older build of this integration, so every
    field in one is untrusted input: a counter that comes back as ``null`` or a
    string must leave the session unseeded rather than raise on the setup path.
    """

    try:
        return None if value is None else float(value)
    except TypeError, ValueError:
        return None


def _snapshot_time(value: Any) -> Optional[datetime]:
    """A timestamp out of a stored snapshot, or ``None`` if it is unusable."""

    if not isinstance(value, str):
        return None
    return dt_util.parse_datetime(value)


def _charge_complete(soc: Optional[float], target: Optional[float]) -> bool:
    """Whether a charge that stopped reached its target (within the 1 % BMW rounds to)."""

    return target is not None and soc is not None and soc >= target - 1.0


class ChargingHost(Protocol):
    """What charging reads and does, from the coordinator."""

    hass: Any
    entry_id: str
    history: Any
    pricing: PricingConfig
    data: Dict[str, Dict[str, DescriptorState]]
    signal_energy: str
    signal_history: str
    signal_soc_estimate: str
    signal_update: str

    @property
    def _cars(self) -> Dict[str, Vehicle]: ...
    def _car(self, vin: str) -> Vehicle: ...
    def _peek(self, vin: str) -> Vehicle: ...
    def _vins_where(self, test: Any) -> list[str]: ...
    def _tracked_cars(self) -> list[tuple[str, SocTracking]]: ...
    def _live_values(self, vin: str, descriptors: Any) -> Dict[str, Any]: ...
    def _plug_state(self, vin: str) -> Optional[bool]: ...
    def _charging_location(self, vin: str) -> Optional[Dict[str, Any]]: ...
    def _home_zone_name(self) -> Optional[str]: ...
    def pricing_for(self, vin: str) -> PricingConfig: ...
    def battery_capacity_kwh(self, vin: str) -> Optional[float]: ...
    def odometer_km(self, vin: str) -> Optional[float]: ...
    def get_session_energy_kwh(self, vin: str) -> Optional[float]: ...
    def learned_meter_rate(self, vin: str) -> Optional[MeterRate]: ...
    def _log_battery_health(self, vin: str) -> None: ...
    def _fire(self, event_type: str, payload: Dict[str, Any]) -> None: ...


class VehicleCharging:
    """The SoC estimate, the energy counters and the charging sessions of one account."""

    def __init__(self, host: ChargingHost) -> None:
        self._host = host

    def set_direct_power(
        self, vin: str, power_w: Optional[float], timestamp: Optional[datetime]
    ) -> None:
        self._host._car(vin).charge.direct_power_w = None if power_w is None else max(power_w, 0.0)
        self._apply_effective_power(vin, timestamp)

    def set_ac_voltage(
        self, vin: str, voltage_v: Optional[float], timestamp: Optional[datetime]
    ) -> None:
        self._host._car(vin).charge.ac_voltage_v = (
            None if voltage_v is None else max(voltage_v, 0.0)
        )
        self._apply_effective_power(vin, timestamp)

    def set_ac_current(
        self, vin: str, current_a: Optional[float], timestamp: Optional[datetime]
    ) -> None:
        self._host._car(vin).charge.ac_current_a = (
            None if current_a is None else max(current_a, 0.0)
        )
        self._apply_effective_power(vin, timestamp)

    def set_ac_phase(
        self, vin: str, phase_value: Optional[Any], timestamp: Optional[datetime]
    ) -> None:
        phase_count: Optional[int] = None
        if phase_value is None:
            phase_count = None
        elif isinstance(phase_value, (int, float)):
            try:
                parsed = int(phase_value)
            except TypeError, ValueError:
                parsed = None
            phase_count = parsed if parsed and parsed > 0 else None
        elif isinstance(phase_value, str):
            match = re.match(r"(\d+)", phase_value)
            if match:
                try:
                    parsed = int(match.group(1))
                except TypeError, ValueError:
                    parsed = None
                phase_count = parsed if parsed and parsed > 0 else None
        self._host._car(vin).charge.ac_phase_count = phase_count
        self._apply_effective_power(vin, timestamp)

    def _derive_ac_power(self, vin: str) -> Optional[float]:
        charge = self._host._peek(vin).charge
        voltage, current, phases = charge.ac_voltage_v, charge.ac_current_a, charge.ac_phase_count
        if voltage is None or current is None or phases is None:
            return None
        return max(voltage * current * phases, 0.0)

    def _compute_effective_power(self, vin: str) -> Optional[float]:
        direct = self._host._peek(vin).charge.direct_power_w
        if direct is not None:
            return direct
        return self._derive_ac_power(vin)

    def _apply_effective_power(self, vin: str, timestamp: Optional[datetime]) -> None:
        charge = self._host._car(vin).charge
        tracking = charge.tracker()
        effective_power = self._compute_effective_power(vin)
        charge.charging_power_w = effective_power
        if effective_power is not None:
            tracking.update_power(effective_power, timestamp)

    def integrate_energy(self, vin: str, now: datetime) -> bool:
        """Accumulate delivered energy from effective charging power.

        Assumes the last-sampled power held constant since the previous tick (a
        left Riemann sum). ``energy_last_time`` is advanced every call —
        including while idle — so a resumed session never integrates over a
        stale gap.

        BMW does *not* sample charging power evenly: it arrives in bursts with
        gaps of an hour or more between them, while this runs on every batch
        *and* on the watchdog tick. Held across such a gap, one unrepresentative
        sample dominates the total — a real session integrated a 3.54 kW reading
        for 162 minutes and recorded 11.10 kWh where the battery took 5.46. So
        the running total is capped by what the pack can actually have absorbed
        (see :meth:`_session_energy_ceiling_wh`); the integration still supplies
        the timing and the curve, it just can't run away from the battery.
        """
        charge = self._host._car(vin).charge
        last, charge.energy_last_time = charge.energy_last_time, now
        tracking = charge.tracking
        power_w = charge.charging_power_w
        if last is None or tracking is None or not tracking.charging_active:
            return False
        # A session resumed across a restart is owed whatever the pack gained
        # while we were away; it can only be measured once a SoC reading from
        # this session lands, so it is attempted here rather than at the resume.
        self._apply_gap_credit(vin)
        if not power_w or power_w <= 0:
            return False
        delta_seconds = (now - last).total_seconds()
        if delta_seconds <= 0:
            return False
        wh = power_w * (delta_seconds / 3600.0)

        # Accumulate raw, expose bounded. Capping each *increment* instead would
        # lose energy for good every time SoC lagged the charge -- and SoC always
        # lags, arriving a whole percent at a time. Bounding the running total
        # lets it catch up the moment the next reading lands, so an honest
        # session ends on its full figure while a runaway one stays pinned to
        # what the battery can hold.
        raw_after = (charge.energy_session_raw_wh or 0.0) + wh
        before = charge.energy_session_wh or 0.0
        charge.energy_session_raw_wh = raw_after
        # Never below what was already released: a session that loses its meter
        # (another car plugs in, the meter resets) falls back to a SoC ceiling
        # that may sit under the meter's count.
        after = max(before, self._bounded_session_wh(vin, raw_after))

        charge.energy_session_wh = after
        # The whole increment is booked, at this instant's mix and price; the
        # ceiling only decides how much of it is released into the session.
        self._record_energy_delta(vin, now, power_w, wh / 1000.0)
        gained = after - before
        if gained <= 0:
            return False
        charge.energy_lifetime_wh = (charge.energy_lifetime_wh or 0.0) + gained
        self._release_held_energy(vin, gained / 1000.0)
        return True

    def _settle_session_energy(self, vin: str) -> None:
        """Release what the session's latest SoC makes room for, one last time.

        The integration only releases energy while the car is charging, but a
        car that sleeps through its charge sends its new SoC in the same batch
        as the status that ends it -- the ceiling rises after the last step that
        could have used it. A MINI Cooper SE filed every charge at the 2 %
        margin that way, 0.6 kWh for a 15 kWh night (#180). So the close looks
        once more. Only against a ceiling: one that cannot be computed (no SoC
        from this session) never releases a raw total it never checked.
        """

        charge = self._host._peek(vin).charge
        raw = charge.energy_session_raw_wh
        if raw is None or self._session_energy_ceiling_wh(vin) is None:
            return
        before = charge.energy_session_wh or 0.0
        after = self._bounded_session_wh(vin, raw)
        gained = after - before
        if self._session_meter_bound_kwh(vin) is not None:
            # Held to the meter, what is still held is the car's power reading
            # above the wallbox's (the i5's runs ~4 % high) or power held across
            # a gap the meter billed as nothing. Its mix and cost were billed
            # from the meter, so all of it is this charge's.
            held = self._host._peek(vin).session.held
            if held is not None:
                held.release(
                    held.battery_kwh,
                    self._host._peek(vin).session.mix,
                    self._host._peek(vin).session.cost,
                )
        if gained <= 0:
            return
        charge.energy_session_wh = after
        charge.energy_lifetime_wh = (charge.energy_lifetime_wh or 0.0) + gained
        self._release_held_energy(vin, gained / 1000.0)
        if debug_enabled():
            _LOGGER.debug(
                "[charge] %s SETTLE +%s kWh at close (soc %s -> %s)",
                vin,
                round(gained / 1000.0, 3),
                charge.energy_session_soc,
                self._session_soc_percent(vin),
            )
        async_dispatcher_send(self._host.hass, self._host.signal_energy, vin)

    def _release_held_energy(self, vin: str, kwh: float) -> None:
        held = self._host._peek(vin).session.held
        if held is not None:
            held.release(kwh, self._host._peek(vin).session.mix, self._host._peek(vin).session.cost)

    def _bounded_session_wh(self, vin: str, raw_wh: float) -> float:
        """The raw running total, held to what the pack can have absorbed."""

        ceiling = self._session_energy_ceiling_wh(vin)
        return raw_wh if ceiling is None else min(raw_wh, ceiling)

    def _session_soc_percent(self, vin: str) -> Optional[float]:
        """The reported SoC, but only if it was read during this session.

        A car that doesn't stream ``batteryManagement.header`` holds the same
        bootstrap reading forever, which would otherwise be recorded as both ends
        of every charge and bound every one of them to the ceiling's bare margin.
        See :func:`soc_is_from_session` for what that costs.
        """

        tracking = self._host._peek(vin).charge.tracking
        if tracking is None or tracking.last_soc_percent is None:
            return None
        if not soc_is_from_session(
            tracking.last_update, self._host._peek(vin).charge.energy_session_start
        ):
            return None
        return tracking.last_soc_percent

    def _session_energy_ceiling_wh(self, vin: str) -> Optional[float]:
        """The live inputs for :func:`energy_ceiling_kwh`, as Wh -- or the meter's.

        The ceiling rises as SoC does, so a charge that keeps going keeps room
        to accrue, and a stream that falls silent -- taking SoC with it -- grants
        none. Reads the *reported* SoC, never the extrapolated one: the estimate
        is advanced from the very charging power this bounds, so using it would
        let the figure authorise its own growth. And never a reading from before
        the session (see :meth:`_session_soc_percent`), which would bound the
        charge by a number that predates it.
        """

        soc_start = self._host._peek(vin).charge.energy_session_soc
        soc_now = self._session_soc_percent(vin)
        capacity = self._host.battery_capacity_kwh(vin)
        metered_kwh = self._session_meter_bound_kwh(vin)
        if metered_kwh is not None:
            return metered_kwh * 1000.0
        ceiling_kwh = energy_ceiling_kwh(soc_start, soc_now, capacity)
        return None if ceiling_kwh is None else ceiling_kwh * 1000.0

    def _session_meter_bound_kwh(self, vin: str) -> Optional[float]:
        """The bound wallbox meter's count, where it bounds this session.

        See :func:`meter_energy_bound_kwh` for when it does.
        """

        builder = self._host._peek(vin).session.builder
        return meter_energy_bound_kwh(
            None if builder is None else builder.metered_so_far_kwh(),
            self._host._peek(vin).charge.energy_session_soc,
            self._session_soc_percent(vin),
            self._host.battery_capacity_kwh(vin),
        )

    def _sample_power_curve(self, vin: str, now: datetime, power_w: float) -> None:
        """Feed the session record's curve and its running SoC.

        Split out from the energy step because the two answer different
        questions: the curve records what the car *reported*, which stays true
        even in a tick where the ceiling let no energy through.
        """

        builder = self._host._peek(vin).session.builder
        if builder is None:
            return
        builder.sample(now, power_w / 1000.0)
        builder.note_soc(self._session_soc_percent(vin))
        if builder.charge_method is None:
            builder.charge_method = streamed_charge_method(
                self._host._live_values(vin, CHARGE_METHOD_DESCRIPTORS)
            )
        # Throttled inside; this is simply the one place that runs for every
        # sample of every open session, which is exactly the cadence a snapshot
        # of an open session wants.
        self._snapshot_open_session(vin)

    def _record_energy_delta(self, vin: str, now: datetime, power_w: float, kwh: float) -> None:
        """Feed the session record from the same delta that moved the counters.

        Sharing the integration step is deliberate: the curve, the energy and
        the cost then describe exactly the same samples and cannot disagree.

        The bound wallbox meter rides the same step, and for the same reason.
        Its advance over this interval is a *measured* grid delta for exactly
        the window the battery-side increment covers, so it can be billed
        directly -- which is what makes the setting's promise ("that exact grid
        figure is used instead") true of the cost and not only of the total.
        Billing the session's grid figure at close instead would price the whole
        charge at whatever the tariff happened to be when the plug came out.

        Booked into the held pool, not the session: the SoC ceiling decides
        afterwards how much of it the session gets (:meth:`_release_held_energy`).
        """

        self._sample_power_curve(vin, now, power_w)
        self._book_increment(vin, kwh)

    def _book_increment(self, vin: str, kwh: float) -> None:
        """Bill one battery-side increment, at the meter where there is one.

        Also called with nothing integrated at the close, so the meter's last
        advance -- after the final integration step, up to a whole update
        interval on a meter that reports once a minute -- is billed too.
        """

        # One read of the wallbox meter, used twice: to bill this increment and
        # to move the session's running total. ``_grid_meter_step`` advances the
        # billing cursor, so it is called on its own line rather than inline --
        # a side effect buried in an argument list is how one gets called twice.
        meter_now = self._session_grid_meter_kwh(vin, self._host._peek(vin).session.builder)
        metered_kwh = self._grid_meter_step(vin, meter_now)
        billable, _source = billable_energy(
            battery_kwh=kwh,
            grid_kwh=metered_kwh,
            loss_percent=self._host.pricing_for(vin).loss_percent,
        )
        builder = self._host._peek(vin).session.builder
        held = self._host._peek(vin).session.held
        # Away from home the house's meters and tariff describe the house, not
        # this charge: no mix, no cost (see ``house_supplies_this_session``).
        if builder is None or house_supplies_this_session(
            builder.location, self._host._home_zone_name()
        ):
            if held is not None:
                # One sample of the house's meters serves both the mix and the
                # cost, so the two can never describe different instants.
                split = held.hold(kwh, billable, self._supply_shares())
                self._bill_split(held.cost, billable, split)
        elif held is not None:
            held.hold(kwh, None, None)
        if builder is not None:
            builder.note_grid_meter(meter_now)

    def _grid_meter_step(self, vin: str, meter_now: Optional[float]) -> Optional[float]:
        """How far the wallbox meter has advanced since the last billed step.

        ``None`` when there is nothing measured to bill -- no meter bound, the
        first step of a session (the baseline has to be established before a
        delta exists), or a meter that went backwards, which is a reset and not
        a negative charge. In each case billing falls back to the battery-side
        figure, exactly as it did before a meter was ever bound.

        A meter that has not moved is a measured zero, not a missing reading. A
        wallbox that lost power while the car's last reported power was held
        across the gap delivered nothing, and a meter that updates once a
        minute bills the whole minute on its next step -- billing the
        battery-side figure in between counted those kilowatt-hours twice.
        """

        if meter_now is None:
            return None
        previous = self._host._peek(vin).session.grid_meter_billed
        self._host._car(vin).session.grid_meter_billed = meter_now
        if previous is None:
            return None
        delta = meter_now - previous
        if delta < 0:
            return None
        return delta

    def fire_charging_event(
        self,
        vin: str,
        was_charging: bool,
        tracking: SocTracking,
        status: str,
    ) -> None:
        """Fire a HA bus event when a charging session begins or ends.

        A stopped session that reached (or exceeded) the configured target SoC
        also fires a dedicated ``complete`` event so automations can distinguish
        "finished as planned" from "unplugged early".
        """
        now_charging = tracking.charging_active
        if self._host._peek(vin).session.restored_at is not None:
            # First word from the stream since a restart left a charge open.
            # Either it is still running -- the same record continues -- or it
            # ended while we were away, in which case it is closed at its last
            # sample rather than now. Deliberately not routed through the
            # flap debounce: that window exists to rejoin a session we were
            # watching, and here the gap has already happened.
            if now_charging:
                self._resume_restored_session(vin)
            elif str(status).strip().upper() in CHARGE_TERMINAL_STATUSES:
                self._close_restored_session(vin, status)
                self._clear_charge_progress(vin)
            # Anything else -- initialization, a pause, a status we don't know --
            # says nothing about whether this charge is over, so the session is
            # left restored and the grace timer keeps the deadline.
            return
        if was_charging == now_charging:
            if (
                not now_charging
                and str(status).strip().upper() in CHARGE_TERMINAL_STATUSES
                and self._host._peek(vin).session.close_timer is None
                and self._host._peek(vin).session.builder is None
            ):
                # The car says no charge is running and none is open here: any
                # progress figure still standing is one whose close we never saw
                # -- the charge ended while Home Assistant was down, or before
                # an update that clears them -- and came back with the restore.
                self._clear_charge_progress(vin)
            return
        soc = (
            tracking.estimated_percent
            if tracking.estimated_percent is not None
            else tracking.last_soc_percent
        )
        payload: Dict[str, Any] = {
            "vin": vin,
            "entry_id": self._host.entry_id,
            "status": status,
            "soc": None if soc is None else round(soc, 1),
            "target_soc": tracking.target_soc_percent,
        }
        if now_charging:
            # Charging resumed while a close was pending: this was a brief status
            # flap, not a new plug-in. Cancel the pending close and let the same
            # session continue -- no fresh STARTED event, no split record.
            if self._cancel_charge_close_timer(vin):
                self._host._car(vin).session.stopped = None
                if debug_enabled():
                    _LOGGER.debug(
                        "[charge] %s RESUME after status flap; keeping session",
                        vin,
                    )
                return
            # New session: zero the session accumulator and stamp its start so
            # the session-energy sensor reports a fresh last_reset.
            started_at = datetime.now(timezone.utc)
            charge = self._host._car(vin).charge
            charge.energy_session_wh = 0.0
            charge.energy_session_raw_wh = 0.0
            charge.energy_session_start = started_at
            # The SoC this session started from, kept here rather than read off
            # the session record so the energy ceiling still applies when history
            # is switched off (the record is optional; the counters are not).
            charge.energy_session_soc = tracking.last_soc_percent
            self._open_session_record(vin, tracking, started_at)
            self._host._fire(EVENT_CHARGING_STARTED, payload)
            return
        # Charging stopped. Debounce the close so a momentary NOCHARGING blip
        # that comes straight back doesn't split one plug-in into two records.
        self._arm_charge_close_timer(vin, status)

    def _arm_charge_close_timer(self, vin: str, status: str) -> None:
        """Commit the session close only if charging stays stopped for the grace.

        Re-arming on each stop transition restarts the countdown, so the close
        fires ``CHARGE_CLOSE_DEBOUNCE_S`` after the *last* stop; a resume in
        between cancels it (see :meth:`_fire_charging_event`). If the cable is
        still in by then, the close waits on until ``CHARGE_PLUGGED_HOLD_S``
        after the stop.
        """

        self._cancel_charge_close_timer(vin)
        self._host._car(vin).session.stopped = (datetime.now(timezone.utc), status)
        self._schedule_charge_close(vin, CHARGE_CLOSE_DEBOUNCE_S)

    def _schedule_charge_close(self, vin: str, delay_s: float) -> None:
        # @callback so HA runs it inline on the event loop; an undecorated timer
        # action is dispatched to the executor thread, and _finalize_charge_close
        # touches loop-only helpers (async_dispatcher_send).
        @callback
        def _fire(_now) -> None:
            self._host._car(vin).session.close_timer = None
            stopped_at, _status = self._host._peek(vin).session.stopped
            if self._plug_state_live(vin) is True:
                waited = (datetime.now(timezone.utc) - stopped_at).total_seconds()
                if waited < CHARGE_PLUGGED_HOLD_S:
                    self._schedule_charge_close(vin, CHARGE_PLUGGED_HOLD_S - waited)
                    return
            self._finalize_charge_close(vin)

        self._host._car(vin).session.close_timer = async_call_later(self._host.hass, delay_s, _fire)

    def close_held_charge_on_unplug(self, vin: str) -> None:
        """Close a charge held for the plug as soon as the cable comes out.

        Only once the flap debounce is over: before that the pending close
        fires on its own, and an unplug inside it is the usual end of a charge.
        """

        if (
            self._host._peek(vin).session.close_timer is None
            or self._plug_state_live(vin) is not False
        ):
            return
        stopped_at, _status = self._host._peek(vin).session.stopped
        if (datetime.now(timezone.utc) - stopped_at).total_seconds() < CHARGE_CLOSE_DEBOUNCE_S:
            return
        self._cancel_charge_close_timer(vin)
        self._finalize_charge_close(vin)

    def _plug_state_live(self, vin: str) -> Optional[bool]:
        return plug_state(self._host._live_values(vin, PLUG_DESCRIPTORS))

    def _cancel_charge_close_timer(self, vin: str) -> bool:
        """Cancel a pending close; return whether one was actually pending."""

        cancel = self._host._peek(vin).session.take("close_timer")
        if cancel is None:
            return False
        cancel()
        return True

    def _finalize_charge_close(self, vin: str, status: Optional[str] = None) -> None:
        """Actually close the debounced session and fire the stop events.

        The record ends when the charge stopped, not when the wait for a resume
        ran out; ``status`` overrides the car's own word for the end.
        """

        stopped_at, stopped_status = self._host._peek(vin).session.take("stopped") or (None, None)
        status = status or stopped_status
        tracking = self._host._peek(vin).charge.tracking
        if tracking is None:
            return
        soc = (
            tracking.estimated_percent
            if tracking.estimated_percent is not None
            else tracking.last_soc_percent
        )
        payload: Dict[str, Any] = {
            "vin": vin,
            "entry_id": self._host.entry_id,
            "status": status,
            "soc": None if soc is None else round(soc, 1),
            "target_soc": tracking.target_soc_percent,
        }
        # Close the record first so its summary can ride along on the event --
        # automations then get the cost without a second lookup.
        session = self._close_session_record(vin, status, at=stopped_at)
        self._clear_charge_progress(vin)
        if session is not None:
            payload["energy_kwh"] = session.energy_kwh
            payload["cost"] = session.cost
            payload["session_id"] = session.id
        payload["complete"] = _charge_complete(soc, tracking.target_soc_percent)
        self._host._fire(EVENT_CHARGING_STOPPED, payload)
        target = tracking.target_soc_percent
        if payload["complete"]:
            self._host._fire(EVENT_CHARGING_COMPLETE, payload)
        elif charging_interrupted(
            soc, target, plug_state(self._host._live_values(vin, PLUG_DESCRIPTORS))
        ):
            # Short of the target with the cable still in, five minutes on (the
            # plugged-in hold) -- not a pause, and not the driver unplugging.
            self._host._fire(
                EVENT_CHARGING_INTERRUPTED, {**payload, "reason": self._charge_end_reason(vin)}
            )

    def _charge_end_reason(self, vin: str) -> Optional[str]:
        """BMW's own word for why the charge ended, when the car sent one live."""

        for value in self._host._live_values(vin, CHARGE_END_REASON_DESCRIPTORS).values():
            if isinstance(value, str) and value.strip().upper() not in ("", "INVALID", "UNKNOWN"):
                return value
        return None

    def _open_session_record(self, vin: str, tracking: SocTracking, started_at: datetime) -> None:
        if self._host.history is None:
            return
        location = self._host._charging_location(vin)
        self._host._car(vin).session.builder = SessionBuilder(
            vin,
            started_at,
            soc_start=tracking.last_soc_percent,
            target_soc=tracking.target_soc_percent,
            location=location,
            location_assumed=location is None,
            soc_before=tracking.soc_before_charge,
        )
        self._host._car(vin).session.cost = CostAccumulator(currency=self._host.pricing.currency)
        self._host._car(vin).session.mix = MixAccumulator()
        self._host._car(vin).session.held = HeldEnergy(currency=self._host.pricing.currency)
        # Where the wallbox meter stood when the charge began. Taken here, at
        # the transition, rather than at the first energy sample -- which can be
        # minutes later, by which time the meter has already counted kilowatt-
        # hours this session would then not be credited with. The billing cursor
        # starts in the same place, so the very first increment is billed at the
        # meter rather than being skipped for want of a baseline.
        meter_at_open = self._session_grid_meter_kwh(vin, self._host._peek(vin).session.builder)
        self._host._peek(vin).session.builder.note_grid_meter(meter_at_open)
        if meter_at_open is None:
            self._host._car(vin).session.grid_meter_billed = None
        else:
            self._host._car(vin).session.grid_meter_billed = meter_at_open
        # Snapshot straight away: a restart in the first five minutes of a charge
        # is exactly as likely as one in any other five, and an unsnapshotted
        # session is the thing this whole mechanism exists to prevent.
        self._snapshot_open_session(vin, force=True)
        if debug_enabled():
            _LOGGER.debug(
                "[charge] %s OPEN at %s zone=%s soc=%s target=%s pricing=%s price_now=%s",
                vin,
                started_at.isoformat(),
                (location or {}).get("zone"),
                tracking.last_soc_percent,
                tracking.target_soc_percent,
                self._host.pricing.mode if self._host.pricing.enabled else "disabled",
                self._current_price(),
            )

    def _snapshot_open_session(self, vin: str, *, force: bool = False) -> None:
        """Write the in-progress charge to the store so a restart can resume it.

        Throttled to ``OPEN_SESSION_SAVE_INTERVAL_S``: every write rewrites the
        whole history document, and a charge samples for hours. ``force`` skips
        the throttle for the moments that matter -- the session opening, and the
        way out on unload or shutdown.
        """

        builder = self._host._peek(vin).session.builder
        if builder is None or self._host.history is None:
            return
        if self._host._peek(vin).session.restored_at is not None:
            # Restored but not yet confirmed by the stream: nothing has been
            # sampled into it since, so there is nothing new to write -- and
            # rewriting it would stamp a fresh ``saved_at`` on an old charge,
            # which is exactly the staleness test that decides whether it may be
            # resumed at all.
            return
        now = datetime.now(timezone.utc)
        if not force:
            last_saved = self._host._peek(vin).session.saved_at
            if (
                last_saved is not None
                and (now - last_saved).total_seconds() < OPEN_SESSION_SAVE_INTERVAL_S
            ):
                return

        snapshot = builder.to_dict()
        accumulator = self._host._peek(vin).session.cost
        snapshot["cost"] = None if accumulator is None else accumulator.to_dict()
        mix = self._host._peek(vin).session.mix
        snapshot["mix"] = None if mix is None else mix.to_dict()
        held = self._host._peek(vin).session.held
        snapshot["held"] = None if held is None else held.to_dict()
        # The counters live on the coordinator, not on the builder (they exist
        # even with history switched off), so they have to ride along or a
        # resumed session would integrate from zero.
        charge = self._host._peek(vin).charge
        snapshot["energy_wh"] = charge.energy_session_wh or 0.0
        snapshot["energy_raw_wh"] = charge.energy_session_raw_wh or 0.0
        snapshot["soc_baseline"] = charge.energy_session_soc
        snapshot["saved_at"] = now.isoformat()
        try:
            self._host.history.async_set_open_session(vin, snapshot)
        except Exception:  # noqa: BLE001 - bookkeeping must not break the stream
            _LOGGER.exception("Could not snapshot the in-progress charge for %s", mask_vin(vin))
            return
        self._host._car(vin).session.saved_at = now

    def _clear_open_session(self, vin: str) -> None:
        """Forget the in-progress charge for one VIN, in store and in memory."""

        self._host._car(vin).session.saved_at = None
        self._host._car(vin).session.restored_at = None
        self._host._car(vin).session.gap_credit_soc = None
        self._host._car(vin).session.grid_meter_billed = None
        self._cancel_restored_open_timer(vin)
        if self._host.history is None:
            return
        with suppress(Exception):
            self._host.history.async_clear_open_session(vin)

    def async_restore_open_sessions(self) -> None:
        """Pick up charges that were still running when we last stopped.

        Runs once at setup, after the store has loaded and before the stream
        connects. A session young enough to still be the same charge is held
        open until the stream says otherwise (see :meth:`_fire_charging_event`);
        an older one is filed immediately as what it already is.
        """

        if self._host.history is None:
            return
        now = datetime.now(timezone.utc)
        for vin, snapshot in self._host.history.open_sessions().items():
            builder = SessionBuilder.from_dict(snapshot)
            if builder is None:
                self._clear_open_session(vin)
                continue
            self._host._car(vin).session.builder = builder
            self._host._car(vin).session.cost = CostAccumulator.from_dict(
                snapshot.get("cost"), currency=self._host.pricing.currency
            )
            self._host._car(vin).session.mix = MixAccumulator.from_dict(snapshot.get("mix"))
            self._host._car(vin).session.held = HeldEnergy.from_dict(
                snapshot.get("held"), currency=self._host.pricing.currency
            )
            energy_wh = _snapshot_float(snapshot.get("energy_wh")) or 0.0
            charge = self._host._car(vin).charge
            charge.energy_session_wh = energy_wh
            charge.energy_session_raw_wh = (
                _snapshot_float(snapshot.get("energy_raw_wh")) or energy_wh
            )
            charge.energy_session_start = builder.start
            charge.energy_session_soc = _snapshot_float(snapshot.get("soc_baseline"))
            # Resume the billing cursor where the snapshot left it, so the
            # kilowatt-hours the wallbox counted while we were away are not
            # billed at whatever the tariff happens to be now. They still reach
            # the record's ``grid_kwh`` -- the meter's own span covers the gap --
            # which is the same division of labour the battery-side gap credit
            # already makes: honest total, no invented price.
            if builder.grid_meter_last is None:
                self._host._car(vin).session.grid_meter_billed = None
            else:
                self._host._car(vin).session.grid_meter_billed = builder.grid_meter_last
            saved_at = _snapshot_time(snapshot.get("saved_at")) or builder.last_sample_at
            self._host._car(vin).session.saved_at = saved_at or now

            if not open_session_is_resumable(saved_at, now):
                if debug_enabled():
                    _LOGGER.debug(
                        "[charge] %s RESTORED stale (saved %s); closing at %s",
                        vin,
                        None if saved_at is None else saved_at.isoformat(),
                        None
                        if builder.last_sample_at is None
                        else builder.last_sample_at.isoformat(),
                    )
                self._close_session_record(
                    vin, "restart", at=builder.last_sample_at or builder.start
                )
                continue

            self._host._car(vin).session.restored_at = saved_at or now
            self._arm_restored_open_timer(vin)
            if debug_enabled():
                _LOGGER.debug(
                    "[charge] %s RESTORED open session from %s energy=%s kWh "
                    "soc=%s->%s; awaiting the stream",
                    vin,
                    builder.start.isoformat(),
                    round(energy_wh / 1000.0, 3),
                    builder.soc_start,
                    builder.soc_end,
                )

    def _arm_restored_open_timer(self, vin: str) -> None:
        """Close a restored session the stream never confirmed either way."""

        self._cancel_restored_open_timer(vin)

        # @callback so HA runs it on the event loop: the close touches
        # async_dispatcher_send, which is loop-only.
        @callback
        def _fire(_now) -> None:
            self._host._car(vin).session.restored_timer = None
            if self._charge_is_evidently_running(vin):
                # BMW has not re-sent the status, but it is still reporting
                # charging power stamped after the snapshot -- which only a
                # running charge produces. Resume rather than split one plug-in
                # into two records.
                self._resume_restored_session(vin)
                tracking = self._host._peek(vin).charge.tracking
                if tracking is not None:
                    tracking.charging_active = True
                return
            self._close_restored_session(vin, "restart")

        self._host._car(vin).session.restored_timer = async_call_later(
            self._host.hass, RESTORED_SESSION_GRACE_S, _fire
        )

    def _charge_is_evidently_running(self, vin: str) -> bool:
        """Whether the stream still shows this restored charge under way.

        Charging power arriving with a timestamp *after* the snapshot can only
        come from a charge that outlived the restart: an open session is
        snapshotted continuously while it samples, so anything newer than the
        last snapshot was reported while we were away or since we came back.
        Deliberately not a plain "is the last power reading non-zero" test --
        that value lingers at its final figure long after the plug comes out.
        """

        tracking = self._host._peek(vin).charge.tracking
        saved_at = self._host._peek(vin).session.restored_at
        if tracking is None or saved_at is None:
            return False
        last_power_time = tracking.last_power_time
        if last_power_time is None or last_power_time <= saved_at:
            return False
        return bool(tracking.last_power_w and tracking.last_power_w > 0)

    def _cancel_restored_open_timer(self, vin: str) -> bool:
        cancel = self._host._peek(vin).session.take("restored_timer")
        if cancel is None:
            return False
        cancel()
        return True

    def _resume_restored_session(self, vin: str) -> None:
        """Carry on with a restored session the stream says is still charging."""

        self._host._car(vin).session.restored_at = None
        self._cancel_restored_open_timer(vin)
        builder = self._host._peek(vin).session.builder
        if builder is not None and builder.soc_end is not None:
            # Claim the charge that ran while nobody was watching, once a SoC
            # reading from *this* session arrives to measure it (see
            # :meth:`_apply_gap_credit`). The pack's SoC rose; that energy is as
            # real as the integrated kind, and the same ceiling bounds both.
            self._host._car(vin).session.gap_credit_soc = builder.soc_end
        if debug_enabled():
            _LOGGER.debug(
                "[charge] %s RESUME restored session (energy=%s kWh so far)",
                vin,
                self._host.get_session_energy_kwh(vin),
            )

    def _close_restored_session(self, vin: str, status: str) -> None:
        """File a restored session the charge did not survive.

        Ends at the last sample we actually watched, never at the moment we
        found out: the car stopped charging somewhere in the gap, and stretching
        the record to now would inflate its duration and its average power.
        """

        builder = self._host._peek(vin).session.builder
        end_at = None if builder is None else (builder.last_sample_at or builder.start)
        session = self._close_session_record(vin, status, at=end_at)
        if session is None:
            return
        payload = {
            "vin": vin,
            "entry_id": self._host.entry_id,
            "status": status,
            "soc": session.soc_end,
            "target_soc": session.target_soc,
            "energy_kwh": session.energy_kwh,
            "cost": session.cost,
            "session_id": session.id,
            "complete": _charge_complete(session.soc_end, session.target_soc),
        }
        self._host._fire(EVENT_CHARGING_STOPPED, payload)
        if payload["complete"]:
            # It reached its target while nobody watched: still the news an
            # automation waits for, by the same rule as a charge seen ending.
            self._host._fire(EVENT_CHARGING_COMPLETE, payload)

    def _apply_gap_credit(self, vin: str) -> None:
        """Credit the SoC a resumed session gained while we were not watching.

        Applied once, and only from a reading taken during the session itself --
        the same test the energy ceiling uses. Added to the *raw* total so the
        ceiling still has the last word: this can give back energy the restart
        cost us, never invent energy the pack never took.
        """

        baseline = self._host._peek(vin).session.gap_credit_soc
        if baseline is None:
            return
        soc_now = self._session_soc_percent(vin)
        if soc_now is None:
            return
        self._host._car(vin).session.gap_credit_soc = None
        capacity = self._host.battery_capacity_kwh(vin)
        gained = soc_now - baseline
        if not capacity or capacity <= 0 or gained <= 0:
            return
        credit_wh = gained / 100.0 * capacity * 1000.0
        charge = self._host._car(vin).charge
        charge.energy_session_raw_wh = (charge.energy_session_raw_wh or 0.0) + credit_wh
        charge.energy_session_wh = self._bounded_session_wh(vin, charge.energy_session_raw_wh)
        if debug_enabled():
            _LOGGER.debug(
                "[charge] %s GAP credit %s kWh (soc %s -> %s across the restart)",
                vin,
                round(credit_wh / 1000.0, 3),
                baseline,
                soc_now,
            )

    def _close_session_record(self, vin: str, status: str, *, at: Optional[datetime] = None):
        # One last look at the wallbox meter, billed and noted before the settle it
        # may bound. The final energy deltas and the close can be minutes apart on
        # a charge that tapers, and those are kilowatt-hours the meter did count.
        if self._host._peek(vin).session.builder is not None:
            self._book_increment(vin, 0.0)
        self._settle_session_energy(vin)
        builder = self._host._peek(vin).session.take("builder")
        accumulator = self._host._peek(vin).session.take("cost")
        mix = self._host._peek(vin).session.take("mix")
        # Whatever is still held, the pack's SoC never accounted for.
        self._host._car(vin).session.held = None
        # Whatever happens below, this charge is no longer in progress: the
        # snapshot has to go even when there is no history to file the record in,
        # or the next start would resurrect it.
        self._clear_open_session(vin)
        if builder is None or self._host.history is None:
            return None

        energy_kwh = self._host.get_session_energy_kwh(vin)
        session = builder.close(
            at or datetime.now(timezone.utc),
            soc_end=self._session_soc_percent(vin),
            energy_kwh=energy_kwh,
            cost=resolve_cost(accumulated=accumulator.as_cost() if accumulator else None),
            reason=status,
        )
        session.mileage_km = self._host.odometer_km(vin)
        session.energy_mix = None if mix is None else mix.as_record()
        if debug_enabled():
            _LOGGER.debug(
                "[charge] %s CLOSE(%s) energy=%s kWh soc=%s->%s zone=%s "
                "assumed=%s odo=%s cost=%s priced/unpriced=%s/%s",
                vin,
                status,
                session.energy_kwh,
                session.soc_start,
                session.soc_end,
                (session.location or {}).get("zone"),
                session.location_assumed,
                session.mileage_km,
                session.cost,
                getattr(accumulator, "priced_kwh", None),
                getattr(accumulator, "unpriced_kwh", None),
            )
        try:
            self._host.history.add_session(session)
        except Exception:  # noqa: BLE001 - never let bookkeeping break the stream
            _LOGGER.exception("Could not record charging session for %s", mask_vin(vin))
            return None
        async_dispatcher_send(self._host.hass, self._host.signal_history, vin)
        self._host._log_battery_health(vin)
        return session

    def _clear_charge_progress(self, vin: str) -> None:
        """Zero BMW's progress figures for a charge that is over.

        Only the ones the car has sent at all, and only when not already zero,
        so a car that never streams them gains no entity and nothing re-renders
        for nothing (see :data:`CHARGE_PROGRESS_DESCRIPTORS`).
        """

        vehicle_state = self._host.data.get(vin)
        if not vehicle_state:
            return
        now = datetime.now(timezone.utc)
        for descriptor in CHARGE_PROGRESS_DESCRIPTORS:
            state = vehicle_state.get(descriptor)
            if state is None or state.value in (0, 0.0):
                continue
            vehicle_state[descriptor] = DescriptorState(
                value=0, unit=state.unit, timestamp=now.isoformat()
            )
            if descriptor == "vehicle.powertrain.electric.battery.charging.power":
                self.set_direct_power(vin, 0.0, now)
            async_dispatcher_send(self._host.hass, self._host.signal_update, vin, descriptor)

    def async_flush_charging(self) -> None:
        """Preserve every in-flight charge on the way out (unload or shutdown).

        Two different states need saving. A session that stopped within the last
        ``CHARGE_CLOSE_DEBOUNCE_S`` still has a timer counting down; cancelling
        it without closing would drop the session, so it is finalized here. A
        session that is still *charging* is snapshotted to the store instead, to
        be picked up by :meth:`async_restore_open_sessions` on the way back in.

        That second half used to be a deliberate omission -- the session was
        dropped, on the assumption BMW's charging-history import would recover
        it. It does not: the import is a manual, quota-costing service. Measured
        on a live instance, two restarts that happened to land mid-charge cost
        about 22 kWh in a single week, and every total built on the ledger
        (energy, cost, statistics, the driving summary's energy balance) read low
        by exactly that much.
        """

        for vin in self._host._vins_where(lambda car: car.session.close_timer is not None):
            if self._cancel_charge_close_timer(vin):
                with suppress(Exception):
                    self._finalize_charge_close(vin, "unload")
        for vin in self._host._vins_where(lambda car: car.session.builder is not None):
            self._snapshot_open_session(vin, force=True)
        # Nothing is waiting on these any more, and a timer left armed across a
        # reload would fire against the outgoing coordinator.
        for vin in self._host._vins_where(lambda car: car.session.restored_timer is not None):
            self._cancel_restored_open_timer(vin)

    def _current_price(self) -> Optional[float]:
        """The price per kWh in force right now, or ``None`` if unknowable.

        Read live rather than at the end of the session: with a dynamic tariff
        the price moves while the car charges, so each energy delta has to be
        billed at the price that applied when it was delivered.
        """

        config = self._host.pricing
        if not config.enabled:
            return None
        if config.mode == MODE_FIXED:
            return config.fixed_price
        state = self._host.hass.states.get(config.price_entity)
        if state is None or state.state in ("unknown", "unavailable", ""):
            return None
        try:
            return float(state.state)
        except TypeError, ValueError:
            return None

    def _power_state_w(self, entity_id: Optional[str]) -> Optional[float]:
        """A power entity's value in watts, or ``None`` if it can't be read.

        Converts from kilowatts where the entity says so. A sensor with no unit
        at all is taken as watts (the Home Assistant default for
        ``device_class: power``), which is stated in the setting's help text --
        silently mixing kW into a watt sum would skew every attribution by a
        factor of a thousand and still look plausible.
        """

        if not entity_id:
            return None
        state = self._host.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable", ""):
            return None
        try:
            value = float(state.state)
        except TypeError, ValueError:
            return None
        unit = (state.attributes.get("unit_of_measurement") or "").strip().lower()
        if unit == "kw":
            return value * 1000.0
        if unit in ("mw",):
            return value * 1_000_000.0
        return value

    def _session_grid_meter_kwh(
        self, vin: str, builder: Optional[SessionBuilder]
    ) -> Optional[float]:
        """``vin``'s wallbox meter as a charge may see it -- ``None`` away from home.

        Every read of the meter on behalf of a session goes through here, so the
        location rule in :func:`meter_counts_this_session` cannot be skipped by a
        new call site (a test pins that). With no session record open there is
        no position to judge, and the reading is returned as it always was.
        """

        if builder is not None and not meter_counts_this_session(
            builder.location, self._host._home_zone_name()
        ):
            return None
        if self._meter_shared(vin):
            # Its advance is both cars' now. The billing cursor goes too, or
            # the first step after the other car stops would bill its energy.
            if builder is not None:
                builder.note_meter_shared()
            self._host._car(vin).session.grid_meter_billed = None
            return None
        return self._grid_meter_kwh(vin)

    def cars_charging_on(self, meter: str, *, exclude: Optional[str] = None) -> list[str]:
        """This account's cars charging at home on ``meter`` right now.

        Asked by every account's coordinator, not just this one: a BMW and a
        MINI on two accounts share one wallbox as easily as two cars on one.
        A car charging elsewhere doesn't count, even with the same meter
        bound (a second car takes the account's meter over by default).
        """

        found: list[str] = []
        for vin, tracking in self._host._tracked_cars():
            # A charge restored across a restart counts while its session waits
            # for the stream's word, not after it is filed unconfirmed: the flag
            # outlives that until the car speaks, which a car done charging may
            # not do for hours, keeping the other car off the meter all along.
            restored = (
                tracking.restored_charging and self._host._peek(vin).session.restored_at is not None
            )
            if vin == exclude or not (tracking.charging_active or restored):
                continue
            if (
                self._host._plug_state(vin) is False
                or self._host.pricing_for(vin).grid_energy_entity != meter
            ):
                continue
            builder = self._host._peek(vin).session.builder
            location = (
                builder.location if builder is not None else self._host._charging_location(vin)
            )
            if meter_counts_this_session(location, self._host._home_zone_name()):
                found.append(vin)
        return found

    def _meter_shared(self, vin: str) -> bool:
        """Whether another car, of any account, charges on ``vin``'s meter now."""

        meter = self._host.pricing_for(vin).grid_energy_entity
        if not meter:
            return False
        if self.cars_charging_on(meter, exclude=vin):
            return True
        return any(other.cars_charging_on(meter) for other in self._other_coordinators())

    def _other_coordinators(self) -> list[Any]:
        entries = getattr(self._host.hass, "config_entries", None)
        if entries is None:
            return []
        found = []
        for entry in entries.async_entries(DOMAIN):
            other = getattr(getattr(entry, "runtime_data", None), "coordinator", None)
            if other is not None and other is not self._host:
                found.append(other)
        return found

    def _grid_meter_kwh(self, vin: str) -> Optional[float]:
        """The cumulative total of ``vin``'s wallbox energy meter, in kWh.

        The wallbox is the only thing in this integration that can *measure*
        what a charge drew from the grid; everything else derives it. Read as a
        running total and differenced over the session (see
        ``sessions.measured_grid_kwh``) rather than trusting a per-session
        counter, because a cumulative ``total_increasing`` energy sensor is what
        every wallbox integration exposes and the only shape that survives a
        Home Assistant restart mid-charge with its history intact.

        Units are honoured -- Wh and MWh sensors are both common -- and a sensor
        with no unit is taken as kWh, which the setting's own help text states
        and is what an energy sensor without a unit almost always is.
        """

        entity_id = self._host.pricing_for(vin).grid_energy_entity
        if not entity_id:
            return None
        state = self._host.hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable", ""):
            return None
        try:
            value = float(state.state)
        except TypeError, ValueError:
            return None
        unit = (state.attributes.get("unit_of_measurement") or "").strip().lower()
        if unit == "wh":
            return value / 1000.0
        if unit == "mwh":
            return value * 1000.0
        return value

    def _supply_shares(self) -> Optional[dict[str, float]]:
        """The site's supply mix right now, or ``None`` if it can't be known.

        Read live for the same reason the price is: a charge that starts in
        sunshine and finishes after dark is half and half, and a single reading
        at either end would describe only that end.
        """

        config = self._host.pricing
        if not config.mix_enabled:
            return None
        battery_w = self._power_state_w(config.battery_power_entity)
        if battery_w is not None and config.battery_power_invert:
            battery_w = -battery_w
        sun = self._host.hass.states.get("sun.sun")
        return supply_shares(
            pv_w=self._power_state_w(config.pv_power_entity),
            grid_w=self._power_state_w(config.grid_power_entity),
            battery_w=battery_w,
            sun_down=sun is not None and sun.state == "below_horizon",
        )

    def _bill_split(
        self,
        accumulator: CostAccumulator,
        billable_kwh: Optional[float],
        split: dict[str, float],
    ) -> None:
        """Bill one energy increment, each source at what that source costs.

        Own solar is billed at ``price_solar`` when the user has set one --
        usually the feed-in tariff, the money given up by not exporting. House
        battery and grid are both billed at the import price in force right now:
        the battery's contents could have come from the roof at noon or from a
        cheap-hour grid charge at three in the morning, and this integration
        cannot tell. Pricing it as solar would quietly claim a saving that may
        never have existed, so the conservative figure stands.
        """

        price_now = self._current_price()
        if not split:
            accumulator.add(billable_kwh, price_now)
            return
        solar_price = self._host.pricing.solar_price
        for source, amount in split.items():
            price = solar_price if source == SOURCE_PV and solar_price is not None else price_now
            accumulator.add(amount, price)

    def apply_soc_estimate(self, vin: str, now: datetime, notify: bool = True) -> bool:
        charge = self._host._peek(vin).charge
        tracking = charge.tracking
        if not tracking:
            changed = any(
                value is not None
                for value in (charge.soc_estimate, charge.soc_rate, charge.charge_eta)
            )
            if vin in self._host._cars:
                for name in (
                    "soc_estimate",
                    "soc_rate",
                    "charge_eta",
                    "charging_power_w",
                    "direct_power_w",
                    "ac_voltage_v",
                    "ac_current_a",
                    "ac_phase_count",
                    "soc_source",
                ):
                    setattr(charge, name, None)
            if notify and changed:
                async_dispatcher_send(self._host.hass, self._host.signal_soc_estimate, vin)
            return changed
        learned = self._meter_rate_if_driving(vin)
        tracking.follow_meter(
            None
            if learned is None
            else self._session_grid_meter_kwh(vin, self._host._peek(vin).session.builder),
            learned,
            now,
        )
        source = "meter" if tracking.meter_model is not None else "stream"
        source_changed = charge.soc_source != source
        charge.soc_source = source
        percent = tracking.estimate(now)
        rate = tracking.current_rate_per_hour()

        rate_changed = False
        if rate in (None, 0):
            if charge.soc_rate is not None:
                charge.soc_rate = None
                rate_changed = True
        else:
            rounded_rate = round(rate, 3)
            if charge.soc_rate != rounded_rate:
                charge.soc_rate = rounded_rate
                rate_changed = True

        estimate_changed = False
        if percent is None:
            if charge.soc_estimate is not None:
                charge.soc_estimate = None
                estimate_changed = True
        else:
            rounded_percent = round(percent, 2)
            if charge.soc_estimate != rounded_percent:
                charge.soc_estimate = rounded_percent
                estimate_changed = True
        eta = self._charge_eta_minutes(vin, tracking)
        eta_changed = charge.charge_eta != eta
        charge.charge_eta = eta
        updated = rate_changed or estimate_changed or source_changed or eta_changed
        if notify and updated:
            async_dispatcher_send(self._host.hass, self._host.signal_soc_estimate, vin)
        return updated

    def _charge_eta_minutes(self, vin: str, tracking: SocTracking) -> Optional[int]:
        """Minutes to the charge target, for the ``charge_eta`` sensor.

        Nothing without a bound wallbox meter: the figure is the meter's, and
        without one the sensor would only ever say 0 or nothing. With one, a
        car that isn't charging needs no time (0), and a charge the meter isn't
        driving -- away from home, the first minutes before its rate settles,
        a paused solar charge -- is ``None``, which leaves the card on BMW's
        own figure (:meth:`SocTracking.minutes_to_target`).
        """

        if not self._host.pricing_for(vin).grid_energy_entity:
            return None
        if not (tracking.charging_active or tracking.restored_charging):
            return 0
        minutes = tracking.minutes_to_target()
        return None if minutes is None else round(minutes)

    def _meter_rate_if_driving(self, vin: str) -> Optional[MeterRate]:
        """The learned rate, if the wallbox meter may drive this car's estimate now.

        Every condition is one the meter itself cannot check, because nothing
        it reports says which car it charged: this car must be charging, not
        reporting its cable out, at home by the ledger's own rule
        (:func:`meter_counts_this_session`), and the only car charging on its
        meter -- with two on one meter, its advance is both of theirs and
        neither may claim it. Cars with a meter each don't get in each other's way. The cable check is for a charging status BMW forgot to
        end: unplugged, the car cannot be what the wallbox is charging. Anything
        else, and the stream drives exactly as it did before a meter was bound.
        """

        tracking = self._host._peek(vin).charge.tracking
        if tracking is None or not (tracking.charging_active or tracking.restored_charging):
            return None
        if self._host._plug_state(vin) is False or self._meter_shared(vin):
            return None
        builder = self._host._peek(vin).session.builder
        location = builder.location if builder is not None else self._host._charging_location(vin)
        if not meter_counts_this_session(location, self._host._home_zone_name()):
            return None
        return self._host.learned_meter_rate(vin)
