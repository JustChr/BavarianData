"""What the car tells ABRP (A Better Routeplanner), and how it is sent.

ABRP plans a route from the battery's real state when a car reports it live
(Iternio's Telemetry API: ``POST /1/tlm/send`` with an API key, the car's token
from the ABRP app, and one JSON point). BavarianData has that state for free off
BMW's stream, so this sends it: the charge level, charging, parked or driving,
range and odometer, and the position only when the user switches that on too.

Home Assistant-free, like ``evcc.py``: the point and the settings are pure
functions, unit-tested without an HA install. ``abrp_push.py`` decides when to
send.

**Unknown sends nothing**, as on the MQTT bridge. A field is left out unless the
car told us; a point without a charge level isn't sent at all, since that is the
one value ABRP is there for. A heartbeat repeats the last known values with a
fresh time (Iternio drops a car it hasn't heard from for a while); it never
makes one up.
"""

from __future__ import annotations

import enum
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

import aiohttp

from .abrp_key import PROJECT_API_KEY
from .car_signals import DESC_GPS_LAT, DESC_GPS_LON, DESC_MOVING
from .const import (
    OPTION_ABRP_API_KEY,
    OPTION_ABRP_ENABLED,
    OPTION_ABRP_POSITION,
    OPTION_ABRP_TOKEN,
)
from .evcc import BridgeSnapshot
from .vehicle_options import ABRP_OPTIONS, cars_with_own

_LOGGER = logging.getLogger(__name__)

SEND_URL = "https://api.iternio.com/1/tlm/send"
TIMEOUT = aiohttp.ClientTimeout(total=15)

# The car's height above sea level, sent with the position (ABRP's
# ``elevation``): it is part of where the car is, so it follows the same switch.
DESC_ALTITUDE = "vehicle.cabin.infotainment.navigation.currentLocation.altitude"
# How the car charges: values start ``AC_`` or ``DC_`` (``AC_TYPE2PLUG``,
# ``DC_DYNAMIC``), or say ``NOCHARGING``.
DESC_CHARGING_METHOD = "vehicle.drivetrain.electricEngine.charging.method"


class SendResult(enum.Enum):
    """How a send went, in the terms the Configure screen reports."""

    OK = "ok"
    # The key or the car's token was refused (HTTP 401/403).
    REJECTED = "rejected"
    # Nothing usable came back: no network, a timeout, ABRP down.
    UNREACHABLE = "unreachable"


@dataclass
class AbrpCar:
    """One car's ABRP settings."""

    enabled: bool = False
    token: str = ""
    position: bool = False

    @property
    def sends(self) -> bool:
        return self.enabled and bool(self.token)


@dataclass
class AbrpConfig:
    """The ABRP settings, read off the config entry's options."""

    # The user's own key, else the one this release ships with.
    api_key: str = ""
    cars: dict[str, AbrpCar] = field(default_factory=dict)

    def car(self, vin: str) -> AbrpCar:
        return self.cars.get(vin) or AbrpCar()

    def sends(self, vin: str) -> bool:
        """Whether ``vin`` is sent to ABRP."""

        return bool(self.api_key) and self.car(vin).sends

    @property
    def any_enabled(self) -> bool:
        return bool(self.api_key) and any(car.sends for car in self.cars.values())

    @classmethod
    def from_options(cls, options: Mapping[str, Any]) -> "AbrpConfig":
        own = cars_with_own(options, ABRP_OPTIONS)
        return cls(
            api_key=api_key(options),
            cars={
                vin: AbrpCar(
                    enabled=bool(values.get(OPTION_ABRP_ENABLED)),
                    token=_text(values.get(OPTION_ABRP_TOKEN)),
                    position=bool(values.get(OPTION_ABRP_POSITION)),
                )
                for vin, values in own.items()
            },
        )


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def api_key(options: Mapping[str, Any]) -> str:
    """The key sends go out with: the user's own, else the shipped one."""

    return _text(options.get(OPTION_ABRP_API_KEY)) or PROJECT_API_KEY.strip()


def dc_charging(method: Any) -> Optional[bool]:
    """Whether the car charges from a DC charger, from BMW's charging method."""

    if not isinstance(method, str):
        return None
    word = method.strip().upper()
    if word.startswith("DC"):
        return True
    if word.startswith("AC"):
        return False
    return None


@dataclass
class AbrpSnapshot:
    """One car as ABRP sees it. Every field but the VIN may be unknown."""

    bridge: BridgeSnapshot
    parked: Optional[bool] = None
    dc: Optional[bool] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude: Optional[float] = None


def _value(coordinator: Any, vin: str, descriptor: str) -> Any:
    state = coordinator.get_state(vin, descriptor)
    return None if state is None else state.value


def _float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except TypeError, ValueError:
        return None
    return None if number != number else number


def _parked(coordinator: Any, vin: str) -> Optional[bool]:
    """Whether the car stands still, from the most direct source it has.

    A car that streams motion says so itself. Otherwise the trip detector does,
    except on a car that reports its trips only once they are over: it never
    has a drive under way, so "no drive" would call it parked on the motorway.
    """

    moving = _value(coordinator, vin, DESC_MOVING)
    if isinstance(moving, bool):
        return not moving
    if coordinator.report_trips(vin):
        return None
    return not coordinator.has_open_trip(vin)


def abrp_snapshot(coordinator: Any, vin: str) -> AbrpSnapshot:
    """What we would tell ABRP about this car now, read off the coordinator."""

    bridge = coordinator.bridge_snapshot(vin)
    dc = dc_charging(_value(coordinator, vin, DESC_CHARGING_METHOD)) if bridge.charging else None
    latitude = _float(_value(coordinator, vin, DESC_GPS_LAT))
    longitude = _float(_value(coordinator, vin, DESC_GPS_LON))
    if latitude is None or longitude is None:
        latitude = longitude = None
    return AbrpSnapshot(
        bridge=bridge,
        parked=_parked(coordinator, vin),
        dc=dc,
        latitude=latitude,
        longitude=longitude,
        altitude=_float(_value(coordinator, vin, DESC_ALTITUDE)),
    )


def _rounded(value: Optional[float], digits: int) -> Optional[float]:
    number = _float(value)
    return None if number is None else round(number, digits)


def telemetry(snapshot: AbrpSnapshot, *, now: datetime, position: bool) -> Optional[dict[str, Any]]:
    """The point ABRP gets, or ``None`` while the charge level is unknown.

    ``utc`` is the time of sending: ABRP orders points by it and drops a car it
    hasn't heard from lately, and every value in the point is still the last
    true one. ``power`` is ABRP's sign, negative while charging; it is known
    while charging and, standing still and not charging, zero. While driving
    the car streams no power, so none is sent.
    """

    bridge = snapshot.bridge
    if bridge.soc is None:
        return None
    point: dict[str, Any] = {
        "utc": int(now.astimezone(timezone.utc).timestamp()),
        "soc": _rounded(bridge.soc, 1),
    }

    def _put(key: str, value: Any) -> None:
        if value is not None:
            point[key] = value

    if bridge.charging is not None:
        point["is_charging"] = 1 if bridge.charging else 0
    if bridge.charging is True:
        power = _rounded(bridge.charge_power_kw, 2)
        _put("power", None if power is None else -power)
        if snapshot.dc is not None:
            point["is_dcfc"] = 1 if snapshot.dc else 0
    elif bridge.charging is False:
        point["is_dcfc"] = 0
        if snapshot.parked is True:
            point["power"] = 0
    if snapshot.parked is not None:
        point["is_parked"] = 1 if snapshot.parked else 0
        if snapshot.parked:
            point["speed"] = 0
    _put("odometer", _rounded(bridge.odometer_km, 1))
    _put("est_battery_range", _rounded(bridge.range_km, 1))
    if position and snapshot.latitude is not None and snapshot.longitude is not None:
        point["lat"] = round(snapshot.latitude, 6)
        point["lon"] = round(snapshot.longitude, 6)
        _put("elevation", _rounded(snapshot.altitude, 0))
    return point


async def async_send(
    session: aiohttp.ClientSession, *, key: str, token: str, point: Mapping[str, Any]
) -> SendResult:
    """Send one point. Never raises: a failed send is a result, not an error."""

    try:
        async with session.post(
            SEND_URL,
            headers={"Authorization": f"APIKEY {key}"},
            data={"token": token, "tlm": json.dumps(dict(point), separators=(",", ":"))},
            timeout=TIMEOUT,
        ) as response:
            if response.status in (401, 403):
                return SendResult.REJECTED
            if response.status != 200:
                _LOGGER.debug("[abrp] send answered HTTP %s", response.status)
                return SendResult.UNREACHABLE
            body = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        _LOGGER.debug("[abrp] send failed: %s", type(err).__name__)
        return SendResult.UNREACHABLE
    if isinstance(body, dict) and body.get("status") == "ok":
        return SendResult.OK
    _LOGGER.debug("[abrp] send refused: %s", body)
    return SendResult.REJECTED


async def async_check(
    session: aiohttp.ClientSession, *, key: str, token: str, now: datetime
) -> SendResult:
    """Whether ABRP takes this key and token, sending a time and nothing else."""

    point = {"utc": int(now.astimezone(timezone.utc).timestamp())}
    return await async_send(session, key=key, token=token, point=point)
