"""What the car looks like to a charge controller, and the config to read it.

BavarianData holds the one thing a charge controller cannot get cheaply: a live
state of charge, pushed over BMW's stream at **zero REST quota**. evcc, openWB
and friends otherwise poll a vendor API on a timer and get rate-limited for it.
This module is the outbound half of that bridge -- it turns the coordinator's
view of a vehicle into a flat set of MQTT payloads, and generates the evcc
``custom`` vehicle block that reads them back.

Home Assistant-free on purpose, like the ``history`` package: the topic layout,
the status mapping and the generated YAML are all pure functions of a snapshot,
so they are unit-tested without an HA install (see ``tests/conftest.py``). The
publishing glue -- which needs ``hass`` and Home Assistant's own MQTT
integration -- lives in ``bridge.py``.

**Unknown publishes nothing.** The house rule of this integration is that a
wrong number is worse than none, and here it has teeth: a charge controller acts
on what we say. A missing SoC makes evcc fall back to its own estimate; a
confidently published ``0`` would make it charge a full battery. So every field
is omitted unless it is actually known, and ``bridge.py`` clears the retained
topic of anything that becomes unknown again rather than leaving a stale value
on the broker forever.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from .const import (
    DEFAULT_BRIDGE_PREFIX,
    DEFAULT_BRIDGE_RETAIN,
    OPTION_BRIDGE_ENABLED,
    OPTION_BRIDGE_PREFIX,
    OPTION_BRIDGE_RETAIN,
)

# evcc's charge-status vocabulary (IEC 61851 states, as used by every evcc
# charger and vehicle implementation): A = no vehicle connected, B = connected
# but not charging, C = charging. Anything else is rejected by evcc, so these
# three are the whole alphabet.
STATUS_DISCONNECTED = "A"
STATUS_CONNECTED = "B"
STATUS_CHARGING = "C"

# Topic suffixes, published under ``<prefix>/<vin>/``. Stable names: a user's
# evcc config quotes them literally, so renaming one silently breaks their
# charging. ``state`` is the whole snapshot as a JSON document, for consumers
# that template one topic (openWB's MQTT SoC module, Node-RED) instead of
# subscribing per field.
TOPIC_SOC = "soc"
TOPIC_STATUS = "status"
TOPIC_RANGE = "range"
TOPIC_ODOMETER = "odometer"
TOPIC_LIMIT_SOC = "limitSoc"
TOPIC_CHARGE_POWER = "chargePower"
TOPIC_PLUGGED = "plugged"
TOPIC_CHARGING = "charging"
TOPIC_UPDATED = "updated"
TOPIC_STATE = "state"

# Every topic this bridge ever owns. ``bridge.py`` uses it to know what it may
# clear from the broker when the bridge is switched off -- it must never delete a
# retained topic it did not publish.
ALL_TOPICS = (
    TOPIC_SOC,
    TOPIC_STATUS,
    TOPIC_RANGE,
    TOPIC_ODOMETER,
    TOPIC_LIMIT_SOC,
    TOPIC_CHARGE_POWER,
    TOPIC_PLUGGED,
    TOPIC_CHARGING,
    TOPIC_UPDATED,
    TOPIC_STATE,
)

# Aliased, not restated: ``const.py`` is what the options flow defaults from,
# and two spellings of the same default is how a topic layout drifts.
DEFAULT_PREFIX = DEFAULT_BRIDGE_PREFIX

# MQTT wildcards and the topic separator cannot appear in a prefix: a publish to
# a wildcard topic is rejected by the broker, and a leading/trailing slash makes
# an empty topic level that is legal but confuses every MQTT browser.
_FORBIDDEN_PREFIX_CHARS = ("#", "+", "\x00")


def normalize_prefix(value: Optional[str]) -> str:
    """A usable topic prefix, falling back to the default rather than failing.

    Accepts what a user is likely to type (``bavariandata/``, ``/evcc/bmw``)
    and returns it without the surrounding slashes. A prefix containing an MQTT
    wildcard is unusable -- the broker would reject every publish -- so it is
    replaced by the default instead of being published into a void.
    """

    if not isinstance(value, str):
        return DEFAULT_PREFIX
    cleaned = value.strip().strip("/").strip()
    if not cleaned:
        return DEFAULT_PREFIX
    if any(char in cleaned for char in _FORBIDDEN_PREFIX_CHARS):
        return DEFAULT_PREFIX
    return cleaned


def evcc_status(
    *, charging: Optional[bool], plugged: Optional[bool]
) -> Optional[str]:
    """Map what the car reports onto evcc's A/B/C, or ``None`` if unknowable.

    Charging outranks the plug: a car that is actively charging is connected
    whatever the plug descriptor says (and plenty of cars stream the charging
    status without streaming a port at all). With no charge running, only the
    plug can tell "connected, waiting" from "not there" -- and if the car
    streams no port descriptor, we say nothing. Guessing ``A`` would tell evcc
    the car had driven off, which on an identifying charger means it stops
    charging the vehicle it is plugged into.
    """

    if charging:
        return STATUS_CHARGING
    if plugged is True:
        return STATUS_CONNECTED
    if plugged is False:
        return STATUS_DISCONNECTED
    return None


# Where a car says whether a cable is in it, most direct first. Cars disagree
# about which of these they stream: a BMW i5 reports only
# ``chargingPort.status``, and the first version of this bridge -- which looked
# at the port boolean and the connector status alone -- published no plug state
# for it at all. Found on the maintainer's own car, plugged in at the time. On
# that car ``chargingPort.status`` was checked against ten days of the recorder:
# ``connected`` ahead of every charge, and never still ``connected`` when a drive
# began (27 trips).
#
# Two plug descriptors are left out on purpose. ``chargingPort.dcStatus``
# describes the DC side only, so its DISCONNECTED says nothing about an AC cable.
# ``chargingPort.combinedStatus`` is not streamable -- it only ever changes on a
# REST poll -- and a charge controller acting on this morning's plug state is
# precisely what the bridge must not cause. A test pins that every entry here is
# streamable.
PLUG_DESCRIPTORS = (
    "vehicle.powertrain.tractionBattery.charging.port.anyPosition.isPlugged",
    "vehicle.body.chargingPort.status",
    "vehicle.body.chargingPort.statusClearText",
    "vehicle.drivetrain.electricEngine.charging.connectorStatus",
)


def plug_state(values: Mapping[str, Any]) -> Optional[bool]:
    """Whether a cable is in the car, from whichever source the car streams.

    ``values`` maps descriptor -> current value. The first source with a real
    answer wins. ``INVALID``, ``-NA-`` and the connector's ``ERROR`` are not
    answers -- a faulted connector is very much still in the socket -- so they
    fall through to the next source instead of being read as "unplugged".
    Case-insensitive, because a value restored after a restart comes back as the
    entity's lower-case state rather than BMW's upper-case original.
    """

    for descriptor in PLUG_DESCRIPTORS:
        value = values.get(descriptor)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            word = value.strip().upper()
            if word == "CONNECTED":
                return True
            if word == "DISCONNECTED":
                return False
    return None


@dataclass
class BridgeSnapshot:
    """One vehicle as a charge controller sees it. Every field may be unknown.

    ``soc`` is the integration's own figure -- the extrapolated estimate while
    charging, the last reading otherwise -- so evcc and the dashboard card can
    never quote different percentages.
    """

    vin: str
    soc: Optional[float] = None
    charging: Optional[bool] = None
    plugged: Optional[bool] = None
    range_km: Optional[float] = None
    odometer_km: Optional[float] = None
    limit_soc: Optional[float] = None
    charge_power_kw: Optional[float] = None
    updated: Optional[datetime] = None

    @property
    def status(self) -> Optional[str]:
        return evcc_status(charging=self.charging, plugged=self.plugged)

    @property
    def is_empty(self) -> bool:
        """True when there is nothing worth publishing yet.

        A bridge that has only ever seen an empty car should leave the broker
        untouched: publishing a vehicle with no SoC and no status gives evcc a
        vehicle it can do nothing with, and retains it forever.
        """

        return self.soc is None and self.status is None


def _number(value: Optional[float], digits: int) -> Optional[str]:
    """A payload for a number, or ``None`` if it isn't one.

    Rounded and rendered without a trailing ``.0`` for whole values, because
    these payloads are read by humans in MQTT Explorer as often as by evcc.
    """

    if value is None:
        return None
    try:
        rounded = round(float(value), digits)
    except (TypeError, ValueError):
        return None
    if rounded != rounded:  # NaN
        return None
    if digits == 0 or rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:g}"


def bridge_payloads(snapshot: BridgeSnapshot) -> dict[str, str]:
    """Topic suffix -> payload for everything this snapshot actually knows.

    Keys are omitted, never zero-filled: see the module docstring. The JSON
    ``state`` document carries the same values plus an epoch timestamp, which is
    what openWB's MQTT SoC module wants and evcc's ``jq`` templates can read.
    """

    payloads: dict[str, str] = {}

    def _put(topic: str, payload: Optional[str]) -> None:
        if payload is not None:
            payloads[topic] = payload

    _put(TOPIC_SOC, _number(snapshot.soc, 1))
    _put(TOPIC_STATUS, snapshot.status)
    _put(TOPIC_RANGE, _number(snapshot.range_km, 0))
    _put(TOPIC_ODOMETER, _number(snapshot.odometer_km, 0))
    _put(TOPIC_LIMIT_SOC, _number(snapshot.limit_soc, 0))
    _put(TOPIC_CHARGE_POWER, _number(snapshot.charge_power_kw, 2))
    if snapshot.plugged is not None:
        _put(TOPIC_PLUGGED, "true" if snapshot.plugged else "false")
    if snapshot.charging is not None:
        _put(TOPIC_CHARGING, "true" if snapshot.charging else "false")

    updated = snapshot.updated
    if updated is not None:
        as_utc = updated.astimezone(timezone.utc)
        _put(TOPIC_UPDATED, as_utc.isoformat())
    else:
        as_utc = None

    if payloads:
        document: dict[str, Any] = {"vin": snapshot.vin}
        for topic in (
            TOPIC_SOC,
            TOPIC_STATUS,
            TOPIC_RANGE,
            TOPIC_ODOMETER,
            TOPIC_LIMIT_SOC,
            TOPIC_CHARGE_POWER,
        ):
            raw = payloads.get(topic)
            if raw is not None:
                document[topic] = raw if topic == TOPIC_STATUS else float(raw)
        if snapshot.plugged is not None:
            document[TOPIC_PLUGGED] = snapshot.plugged
        if snapshot.charging is not None:
            document[TOPIC_CHARGING] = snapshot.charging
        if as_utc is not None:
            document[TOPIC_UPDATED] = as_utc.isoformat()
            document["updated_ts"] = int(as_utc.timestamp())
        payloads[TOPIC_STATE] = json.dumps(document, separators=(",", ":"))

    return payloads


def bridge_topics(prefix: str, vin: str, payloads: dict[str, str]) -> dict[str, str]:
    """Full topic -> payload, under ``<prefix>/<vin>/<suffix>``.

    The VIN is the topic level because one Home Assistant can hold two BMW
    accounts and several cars, and evcc addresses each vehicle separately. It is
    not a secret to the user's own broker -- it is already the device name in
    Home Assistant -- but it is why the bridge is off by default.
    """

    base = f"{normalize_prefix(prefix)}/{vin}"
    return {f"{base}/{suffix}": payload for suffix, payload in payloads.items()}


def evcc_vehicle_name(vin: str) -> str:
    """An evcc vehicle id for this car: ``bmw_<last six of the VIN>``.

    evcc identifies vehicles by this name in its config, its API and its
    database, so it has to be stable and safe for a YAML key. The last six
    characters of a VIN are its serial -- unique within a user's garage without
    writing the whole VIN into a config file they may well paste into a forum.
    """

    tail = "".join(char for char in vin if char.isalnum())[-6:].lower()
    return f"bmw_{tail}" if tail else "bmw"


def evcc_yaml(
    *,
    prefix: str,
    vin: str,
    title: Optional[str] = None,
    capacity_kwh: Optional[float] = None,
    topics: Optional[tuple[str, ...]] = None,
) -> str:
    """The evcc ``custom`` vehicle block that reads this car off the broker.

    Generated rather than documented as a static example for the same reason the
    Data Selection snippet is: it carries the user's own VIN, their own topic
    prefix and their own pack size, so it is paste-ready instead of being a
    template to edit and get wrong.

    ``timeout`` is deliberately **not** emitted. Values are published retained
    and republished on a heartbeat (see ``bridge.py``), so evcc always has a
    current value -- and a timeout would blank the SoC of a car that is merely
    parked, which is most cars most of the time.
    """

    available = set(topics or ALL_TOPICS)
    base = f"{normalize_prefix(prefix)}/{vin}"
    lines = [
        "vehicles:",
        f"  - name: {evcc_vehicle_name(vin)}",
        "    type: custom",
    ]
    if title:
        lines.append(f"    title: {json.dumps(title)}")
    capacity = _number(capacity_kwh, 1)
    if capacity is not None:
        lines.append(f"    capacity: {capacity}")
    # Ordered as evcc's own documentation lists them, and only for topics this
    # car actually publishes: a plugin pointed at a topic that never gets a
    # message makes evcc log a read error on every update cycle.
    for key, suffix in (
        ("soc", TOPIC_SOC),
        ("status", TOPIC_STATUS),
        ("range", TOPIC_RANGE),
        ("odometer", TOPIC_ODOMETER),
        ("limitSoc", TOPIC_LIMIT_SOC),
    ):
        if suffix not in available:
            continue
        lines.append(f"    {key}:")
        lines.append("      source: mqtt")
        lines.append(f"      topic: {base}/{suffix}")
    return "\n".join(lines) + "\n"


@dataclass
class BridgeConfig:
    """The bridge's settings, read off the config entry's options.

    A dataclass rather than dictionary lookups scattered through the publisher
    so the "is this on?" question has exactly one answer, and so the prefix is
    normalized once, at the edge, instead of on every publish.
    """

    enabled: bool = False
    prefix: str = DEFAULT_BRIDGE_PREFIX
    retain: bool = DEFAULT_BRIDGE_RETAIN

    @classmethod
    def from_options(cls, options: dict[str, Any]) -> "BridgeConfig":
        return cls(
            enabled=bool(options.get(OPTION_BRIDGE_ENABLED)),
            prefix=normalize_prefix(options.get(OPTION_BRIDGE_PREFIX)),
            retain=bool(options.get(OPTION_BRIDGE_RETAIN, DEFAULT_BRIDGE_RETAIN)),
        )
