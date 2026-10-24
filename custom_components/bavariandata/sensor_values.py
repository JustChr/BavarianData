"""What the derived sensors show, apart from Home Assistant.

The sensors in ``sensor.py`` read their figures off the coordinator and its
history; what they make of them -- the state, which attributes ride along and
under which names -- is decided here, where the test suite can check it
without Home Assistant (``tests/test_sensor_values.py``). The attribute names
are a contract: the dashboard card and users' automations read them.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from .history.health import MIN_SAMPLES

# How many trend points to publish in the battery-health attributes. Battery
# health changes at most once per charge, so this attribute is rewritten rarely
# -- but the series still has to stay small enough not to bloat the recorder row.
HEALTH_TREND_POINTS = 60

# Our wheel slugs -> the axle token BMW's streamed tire descriptors use, so a
# diagnosis entity and a pressure entity for the same wheel share a card slot.
AXLE_ROWS = {"front": "row1", "rear": "row2"}

MILEAGE_ATTRIBUTES = (
    "kind",
    "status",
    "start",
    "end",
    "allowance_km",
    "used_km",
    "plan_km",
    "remaining_km",
    "days_left",
    "km_per_day_left",
    "projected_km",
    "projected_over_km",
)


def enum_state(value: Any, options: list[str]) -> tuple[Any, list[str]]:
    """An enum reading as its option, and the options that accept it.

    Options and translation state keys are lowercase slugs; BMW sends ALL_CAPS
    tokens (and old installs stored them), so a string is lowercased. A value
    the catalogue did not document extends the options, so Home Assistant
    accepts it instead of logging a validation error; it shows untranslated.
    """

    if not isinstance(value, str):
        return value, options
    value = value.lower()
    if value not in options:
        options = [*options, value]
    return value, options


def quota_attributes(quota: Any, prefix: str = "api_quota_") -> dict[str, Any]:
    """The REST quota's used/remaining and next reset, when there is a quota."""

    if not quota:
        return {}
    attrs: dict[str, Any] = {
        f"{prefix}used": quota.used,
        f"{prefix}remaining": quota.remaining,
    }
    if next_reset := quota.next_reset_iso:
        attrs[f"{prefix}next_reset"] = next_reset
    return attrs


def connection_attributes(
    debug_info: Mapping[str, Any],
    last_disconnect_reason: Optional[str],
    quota: Any,
    display_units: Mapping[str, Any],
) -> dict[str, Any]:
    """The connection-status sensor's attributes."""

    attrs = dict(debug_info)
    if last_disconnect_reason:
        attrs["last_disconnect_reason"] = last_disconnect_reason
    attrs.update(quota_attributes(quota))
    # Configure -> Units, for the dashboard card: it has no other way to read
    # the account's options.
    attrs["display_units"] = dict(display_units)
    return attrs


def cost_month_attributes(summary: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "sessions": summary.get("sessions", 0),
        "energy_kwh": summary.get("energy_kwh"),
        # Flags a total that is a floor, not a figure: a session was charged
        # while the price was unknown, or one has no cost at all (charged away
        # from home, or before a tariff was set).
        "partial": summary.get("partial", False),
    }


def session_cost(session: Any) -> Any:
    """What the most recent session cost, or ``None`` for no session or no cost."""

    if session is None or not session.cost:
        return None
    return session.cost.get("amount")


def session_cost_attributes(session: Any) -> dict[str, Any]:
    if session is None:
        return {}
    attrs: dict[str, Any] = {
        "energy_kwh": session.energy_kwh,
        "duration_s": session.duration_s,
        "soc_start": session.soc_start,
        "soc_end": session.soc_end,
        "peak_power_kw": session.peak_power_kw,
    }
    if session.location:
        attrs["zone"] = session.location.get("zone")
        if session.location.get("address"):
            attrs["address"] = session.location.get("address")
    attrs["location_assumed"] = session.location_assumed
    if session.cost:
        attrs["cost_source"] = session.cost.get("source")
        attrs["partial"] = bool(session.cost.get("partial"))
    return attrs


def energy_month_attributes(summary: Mapping[str, Any]) -> dict[str, Any]:
    """This month's source mix -- as attributes, not as new entities.

    The history layer's entity budget is spent (one entity per question a user
    actually asks), and "how much of my charging was sun?" is asked of the same
    number the energy sensor already reports. ``solar_percent`` is lifted to the
    top level because it is the figure people want; the kWh behind it stay in
    ``energy_mix`` for anything that wants to check the arithmetic.

    Absent entirely when nothing could be attributed, which is deliberately
    distinct from a mix saying none of it was solar.
    """

    mix = summary.get("energy_mix")
    if not mix:
        return {}
    attrs: dict[str, Any] = {"energy_mix": mix}
    if mix.get("solar_percent") is not None:
        attrs["solar_percent"] = mix["solar_percent"]
    return attrs


def battery_health_state(health: Any) -> Any:
    """Never a number we aren't sure of.

    While learning, the state names the progress instead of a figure that would
    jump as samples trickle in.
    """

    if not health.confident:
        return f"Learning ({min(health.samples, MIN_SAMPLES)}/{MIN_SAMPLES})"
    return health.usable_kwh


def battery_health_attributes(health: Any, trend: Optional[list]) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "samples": health.samples,
        "samples_needed": MIN_SAMPLES,
        "confident": health.confident,
        "usable_capacity_kwh": health.usable_kwh,
        "nominal_capacity_kwh": health.nominal_kwh,
        "vs_new_percent": health.vs_new_percent,
        # A capacity figure that disagrees with BMW's own is being withheld; say
        # so rather than leaving the sensor stuck at "Learning" with no reason.
        "suspicious": health.suspicious,
    }
    if trend is not None:
        attrs["trend"] = trend
    return attrs


def real_range_state(profile: Mapping[str, Any]) -> Any:
    return (profile.get("range") or {}).get("now_km")


def real_range_attributes(profile: Mapping[str, Any]) -> dict[str, Any]:
    ranges = profile.get("range") or {}
    consumption = profile.get("consumption") or {}
    grid = profile.get("grid_consumption") or {}
    return {
        # Why there is no figure, when there is none: "not enough charging
        # history yet" and "this car never streamed its capacity" want different
        # things from the user.
        "status": profile.get("status"),
        "range_full_km": ranges.get("full_km"),
        # The same figure as the state, in kilometres whatever the user's unit
        # system converts the state into -- the card renders kilometres from the
        # ledger throughout, and reading them off a converted state is exactly
        # how a display unit once leaked into a stored value (issue #7).
        "range_now_km": ranges.get("now_km"),
        "soc_percent": ranges.get("soc_percent"),
        "consumption_kwh_per_100km": consumption.get("kwh_per_100km"),
        # Always beside the figure: a consumption number without its side is
        # ambiguous by exactly the size of the charging loss.
        "consumption_source": consumption.get("source"),
        "consumption_window_days": consumption.get("window_days"),
        "consumption_distance_km": consumption.get("distance_km"),
        "grid_consumption_kwh_per_100km": grid.get("kwh_per_100km"),
        "measured_loss_percent": profile.get("measured_loss_percent"),
        "capacity_kwh": profile.get("capacity_kwh"),
        "capacity_source": profile.get("capacity_source"),
        "bmw_range_km": ranges.get("bmw_km"),
        "bmw_range_full_km": ranges.get("bmw_full_km"),
        "vs_bmw_percent": ranges.get("vs_bmw_percent"),
    }


def driving_month_attributes(summary: Mapping[str, Any]) -> dict[str, Any]:
    split = summary.get("split") or {}
    return {
        "trip_count": summary.get("trip_count", 0),
        "business_km": split.get("business_km"),
        "private_km": split.get("private_km"),
        "commute_km": split.get("commute_km"),
        "unclassified_km": split.get("unclassified_km"),
    }


def mileage_attributes(allowance: Mapping[str, Any]) -> dict[str, Any]:
    return {key: allowance.get(key) for key in MILEAGE_ATTRIBUTES}


def tyre_attributes(
    base: Mapping[str, Any], diagnosis: Mapping[str, Any], cluster_name: str
) -> dict[str, Any]:
    """What every tyre-diagnosis sensor carries, on top of the entity's ``base``."""

    attrs = dict(base)
    # These have no catalogue descriptor, so the cluster the card groups by has
    # to be declared here. Must stay present even when unavailable.
    attrs["cluster"] = "tire"
    attrs["cluster_name"] = cluster_name
    # When the diagnosis was last fetched. Worth exposing: it is refreshed once
    # a day at most and survives restarts, so a reading can legitimately be a
    # day old. Mirrored onto ``timestamp`` so these carry the same "as of"
    # attribute as every streamed entity.
    if fetched_at := diagnosis.get("fetched_at"):
        attrs["fetched_at"] = fetched_at
        attrs.setdefault("timestamp", fetched_at)
    return attrs


def wheel(diagnosis: Mapping[str, Any], position: str) -> Mapping[str, Any]:
    return (diagnosis.get("wheels") or {}).get(position) or {}


def wheel_state(wheel_data: Mapping[str, Any]) -> Any:
    return wheel_data.get("wear_status_color") or wheel_data.get("wear_status")


def wheel_attributes(position: str, wheel_data: Mapping[str, Any]) -> dict[str, Any]:
    """Land on the same wheel slot as the streamed pressure/temperature sensors.

    The card keys wheels by ``<tire_axle>_<tire_side>``, and those come from
    BMW's descriptor path, which numbers axles rather than naming them
    (``vehicle.chassis.axle.row1.wheel.left.tire.pressure``).
    """

    axle, side = position.split("_", 1)
    return {
        "tire_axle": AXLE_ROWS[axle],
        "tire_side": side,
        "tire_metric": "diagnosis",
        **wheel_data,
    }


def tyre_status_attributes(diagnosis: Mapping[str, Any]) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "label": diagnosis.get("aggregated_label"),
        "wheels_reported": sorted(diagnosis.get("wheels") or {}),
    }
    if errors := diagnosis.get("errors") or []:
        # Surfaced rather than swallowed: an upstream outage is why the wheels
        # went empty, and that is worth being able to see.
        attrs["errors"] = errors
    return attrs
