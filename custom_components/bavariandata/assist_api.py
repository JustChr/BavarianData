"""The BavarianData LLM API: tools a conversation agent can ask about the car.

Picked under *Settings → Voice assistants → (assistant) → Conversation agent →
Control Home Assistant*, next to Assist. The tools read what the card reads --
the live state and the stored history -- so they cost no BMW quota, and the
history answers go through the integration's own read services, so a tool and
an automation can never disagree about which car or which numbers.

What an answer may contain is decided in ``assist.py`` (HA-free, tested): no
VIN, no coordinates, no trip ids. This file only gathers the inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Optional

import voluptuous as vol

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util
from homeassistant.util.json import JsonObjectType

from . import account_vins
from .assist import (
    CHECKED,
    charging_answer,
    in_unit,
    open_items,
    range_check,
    ranges,
    scrub,
    status_answer,
)
from .const import DOMAIN
from .coordinator import DESC_BMW_RANGE, DESC_FUEL_LEVEL, DESC_GPS_LAT
from .distance import car_distance_unit

API_ID = DOMAIN
API_NAME = "BavarianData"
DESC_GPS_LON = DESC_GPS_LAT.replace("latitude", "longitude")
# Battery and tank together on a plug-in hybrid (``bmw_range_km`` says why it
# isn't the electric range).
DESC_TOTAL_RANGE = "vehicle.drivetrain.lastRemainingRange"

_PROMPT = (
    "These tools answer questions about the user's BMW or MINI cars from "
    "BavarianData. They are read-only: they cannot lock the car, start climate "
    "or charging. The values are the last ones the car sent; a parked car sends "
    "little, so a reading may be hours old and still current. Ranges are "
    "estimates from the user's own recent driving; cold weather and motorway "
    "speeds shorten them. On a plug-in hybrid, electric_range is the battery "
    "alone, fuel_range the tank alone and total_range both. Each answer says "
    "its unit in the field name. Cars: {cars}."
)


@dataclass(frozen=True, slots=True)
class _Car:
    name: str
    vin: str
    runtime: Any


@callback
def _cars(hass: HomeAssistant) -> list[_Car]:
    """Every car of every loaded account, each with a name to call it by."""

    cars: list[_Car] = []
    taken: set[str] = set()
    for entry in hass.config_entries.async_entries(DOMAIN):
        runtime = getattr(entry, "runtime_data", None)
        if runtime is None:
            continue
        coordinator = runtime.coordinator
        for vin in account_vins(hass, entry, coordinator):
            base = coordinator.names.get(vin)
            if not base or base == vin:
                base = "Car"
            name, number = base, 2
            while name in taken:
                name, number = f"{base} ({number})", number + 1
            taken.add(name)
            cars.append(_Car(name, vin, runtime))
    return cars


def _picked(cars: list[_Car], tool_args: dict[str, Any]) -> list[_Car]:
    """The car the model named, or every car when it named none."""

    name = tool_args.get("car")
    if not name:
        return cars
    picked = [car for car in cars if car.name == name]
    if not picked:
        raise HomeAssistantError(f"No car called {name!r}; the cars are {[c.name for c in cars]}")
    return picked


def _per_car(answers: list[dict[str, Any]]) -> JsonObjectType:
    return answers[0] if len(answers) == 1 else {"cars": answers}


class _CarTool(llm.Tool):
    """A tool that answers per car, with an optional ``car`` argument."""

    extra: dict[Any, Any] = {}

    def __init__(self, cars: list[_Car]) -> None:
        self.cars = cars
        schema = dict(self.extra)
        if len(cars) > 1:
            schema[
                vol.Optional(
                    "car",
                    description="Which car. Leave out to answer for every car.",
                )
            ] = vol.In([car.name for car in cars])
        self.parameters = vol.Schema(schema)

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        answers = []
        for car in _picked(self.cars, tool_input.tool_args):
            answer = await self.answer(hass, car, tool_input.tool_args, llm_context)
            unit = car_distance_unit(hass, car.runtime.coordinator, car.vin)
            answers.append({"car": car.name, **in_unit(scrub(answer), unit)})
        return _per_car(answers)

    async def answer(
        self,
        hass: HomeAssistant,
        car: _Car,
        args: dict[str, Any],
        llm_context: llm.LLMContext,
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def _service(
        self,
        hass: HomeAssistant,
        service: str,
        car: _Car,
        llm_context: llm.LLMContext,
        **data: Any,
    ) -> dict[str, Any]:
        response = await hass.services.async_call(
            DOMAIN,
            service,
            {"vin": car.vin, **data},
            blocking=True,
            return_response=True,
            context=llm_context.context,
        )
        return dict(response or {})


def _range(car: _Car) -> tuple[Optional[float], Optional[float]]:
    """The range left and a full battery's range, measured first, then BMW's."""

    coordinator = car.runtime.coordinator
    measured = {}
    if car.runtime.history is not None:
        measured = coordinator.efficiency(car.vin).get("range") or {}
    now = measured.get("now_km")
    full = measured.get("full_km") or measured.get("bmw_full_km")
    return (now if now is not None else coordinator.bmw_range_km(car.vin)), full


def _split_range(
    car: _Car, electric_km: Optional[float]
) -> tuple[Optional[float], Optional[float]]:
    """The electric and the fuel range left (``assist.ranges``)."""

    coordinator = car.runtime.coordinator
    return ranges(
        hybrid=coordinator.is_plug_in_hybrid(car.vin),
        combustion=coordinator.is_combustion_only(car.vin),
        electric_km=electric_km,
        car_electric_km=coordinator.numeric(car.vin, DESC_BMW_RANGE),
        car_total_km=coordinator.numeric(car.vin, DESC_TOTAL_RANGE),
    )


class CarStatusTool(_CarTool):
    name = "get_car_status"
    description = (
        "The car right now: battery, charging, range (electric, fuel and total "
        "on a plug-in hybrid), fuel level, where it is, whether it is being "
        "driven, and anything open or unlocked."
    )

    async def answer(self, hass, car, args, llm_context):
        coordinator = car.runtime.coordinator
        snapshot = coordinator.bridge_snapshot(car.vin)
        range_now, fuel_range = _split_range(car, _range(car)[0])
        latitude = coordinator.get_state(car.vin, DESC_GPS_LAT)
        longitude = coordinator.get_state(car.vin, DESC_GPS_LON)
        zone = None
        has_position = False
        try:
            lat, lon = float(latitude.value), float(longitude.value)
        except AttributeError, TypeError, ValueError:
            pass
        else:
            has_position = True
            zone = coordinator.zone_at(lat, lon)
        readings = {
            descriptor: state.value
            for descriptor, state in coordinator.data.get(car.vin, {}).items()
            if CHECKED.match(descriptor)
        }
        updated = snapshot.updated
        return status_answer(
            name=car.name,
            soc=snapshot.soc,
            charging=snapshot.charging,
            plugged=snapshot.plugged,
            target_soc=snapshot.limit_soc,
            charge_power_kw=snapshot.charge_power_kw,
            minutes_to_target=coordinator.get_charge_eta(car.vin),
            range_km=range_now,
            bmw_range_km=snapshot.range_km,
            fuel_range_km=fuel_range,
            fuel_percent=None
            if fuel_range is None
            else coordinator.numeric(car.vin, DESC_FUEL_LEVEL),
            odometer_km=snapshot.odometer_km,
            zone=zone,
            has_position=has_position,
            trip_in_progress=coordinator.has_open_trip(car.vin),
            open_now=open_items(readings),
            soc_updated=None if updated is None else dt_util.as_local(updated).isoformat(),
        )


class RangeCheckTool(_CarTool):
    name = "check_range"
    description = (
        "Whether a drive fits the car's range left, with a reserve, and how "
        "much more charge it needs if not. A plug-in hybrid counts battery and "
        "fuel together and says whether the battery alone does it. Tell the "
        "user range_left_on_arrival_km, the range they still have after the "
        "drive; safety_margin_km is only the minimum it must stay above. Give "
        "the distance; estimate it from your own knowledge when the user names "
        "places."
    )
    extra = {
        vol.Required("distance_km", description="One way, in kilometers."): vol.Coerce(float),
        vol.Optional("round_trip", description="True when the car has to make it back too."): bool,
    }

    async def answer(self, hass, car, args, llm_context):
        range_now, full = _range(car)
        range_now, fuel_range = _split_range(car, range_now)
        return range_check(
            args["distance_km"],
            round_trip=bool(args.get("round_trip")),
            range_km=range_now,
            full_range_km=full,
            soc=car.runtime.coordinator.current_soc(car.vin),
            fuel_range_km=fuel_range,
        )


class ChargingHistoryTool(_CarTool):
    name = "get_charging_history"
    description = (
        "Charging sessions over the last days: when, where, battery before and "
        "after, energy and cost, with totals."
    )
    extra = {
        vol.Optional("days", description="How many days back. 30 if left out."): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=366)
        ),
    }

    async def answer(self, hass, car, args, llm_context):
        days = int(args.get("days") or 30)
        since = (dt_util.now() - timedelta(days=days)).date().isoformat()
        response = await self._service(
            hass, "get_charging_sessions", car, llm_context, **{"from": since}
        )
        return charging_answer(response.get("sessions") or [], days=days)


class DrivingMonthTool(_CarTool):
    name = "get_driving_month"
    description = (
        "One month of driving: distance, trips, the business/private/commute "
        "split, consumption, cost and the most visited places, against the "
        "month before."
    )
    extra = {
        vol.Optional("month", description="YYYY-MM. This month if left out."): vol.Match(
            r"^\d{4}-\d{2}$"
        ),
    }

    async def answer(self, hass, car, args, llm_context):
        data = {"month": args["month"]} if args.get("month") else {}
        return await self._service(hass, "get_driving_summary", car, llm_context, **data)


class MileageTool(_CarTool):
    name = "get_mileage"
    description = (
        "Odometer, distance this month and year, the yearly projection, and the "
        "lease or yearly mileage allowance with its balance, when one is set."
    )

    async def answer(self, hass, car, args, llm_context):
        return await self._service(hass, "get_mileage_report", car, llm_context)


class EfficiencyTool(_CarTool):
    name = "get_efficiency"
    description = (
        "Measured consumption, the real range it gives, how it compares with "
        "the car's own estimate, cost per distance and the monthly trend."
    )

    async def answer(self, hass, car, args, llm_context):
        return await self._service(hass, "get_efficiency", car, llm_context)


_TOOLS = (
    CarStatusTool,
    RangeCheckTool,
    ChargingHistoryTool,
    DrivingMonthTool,
    MileageTool,
    EfficiencyTool,
)


class BavarianDataAPI(llm.API):
    """The car's tools, offered to every conversation agent that picks them."""

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        cars = _cars(self.hass)
        names = ", ".join(car.name for car in cars) or "none set up yet"
        return llm.APIInstance(
            api=self,
            api_prompt=_PROMPT.format(cars=names),
            llm_context=llm_context,
            tools=[tool(cars) for tool in _TOOLS] if cars else [],
        )


@callback
def async_register_llm_api(hass: HomeAssistant) -> None:
    """Offer the API to conversation agents, once for the whole HA run."""

    llm.async_register_api(hass, BavarianDataAPI(hass=hass, id=API_ID, name=API_NAME))
