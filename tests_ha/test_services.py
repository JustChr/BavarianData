"""Every service, called through a real Home Assistant.

What the services work out is tested HA-free (``tests/test_service_answers.py``,
``tests/test_service_target.py``); this is the half only Home Assistant runs:
the schemas, finding the account and the car, the stores read and written, the
response, and the error a failed call raises. BMW answers as an unreachable
BMW does (``no_bmw``), unless a test says otherwise.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from custom_components.bavariandata.const import DOMAIN
from custom_components.bavariandata.history.models import ChargingSession
from custom_components.bavariandata.history.trips import Trip
from custom_components.bavariandata.stream_activation import (
    StreamActivationError,
    StreamActivationResult,
)

from .conftest import VIN, make_entry, set_up

SOC = "vehicle.drivetrain.batteryManagement.header"
ODOMETER = "vehicle.vehicle.travelledDistance"


async def _account(hass: HomeAssistant):
    entry = make_entry(vehicle_metadata={VIN: {"modelName": "i5 eDrive40", "brand": "BMW"}})
    await set_up(hass, entry)
    runtime = entry.runtime_data
    stamp = dt_util.utcnow().isoformat()
    await runtime.coordinator.async_handle_message(
        {
            "vin": VIN,
            "data": {
                SOC: {"value": 63, "unit": "%", "timestamp": stamp},
                ODOMETER: {"value": 12000, "unit": "km", "timestamp": stamp},
            },
        }
    )
    now = dt_util.utcnow()
    trip = Trip(vin=VIN, start=now - timedelta(hours=2), end=now - timedelta(hours=1))
    trip.distance_km = 40.0
    runtime.history.add_trip(trip)
    runtime.history.add_session(
        ChargingSession(
            vin=VIN,
            start=now - timedelta(hours=6),
            end=now - timedelta(hours=4),
            soc_start=20,
            soc_end=80,
            energy_kwh=48.0,
            location={"zone": "home"},
        )
    )
    await hass.async_block_till_done()
    return entry, runtime, trip


async def _call(hass: HomeAssistant, service: str, data: dict | None = None, **kwargs):
    return await hass.services.async_call(
        DOMAIN, service, {"vin": VIN, **(data or {})}, blocking=True, **kwargs
    )


async def _ask(hass: HomeAssistant, service: str, data: dict | None = None) -> dict:
    return await _call(hass, service, data, return_response=True)


# -- reading the stores -------------------------------------------------------


async def test_the_recorded_history_reads_back(hass: HomeAssistant, no_bmw) -> None:
    _entry, _runtime, trip = await _account(hass)

    sessions = await _ask(hass, "get_charging_sessions", {"limit": 5})
    assert [s["energy_kwh"] for s in sessions["sessions"]] == [48.0]
    places = await _ask(hass, "get_charging_places")
    assert "places" in places
    trips = await _ask(hass, "get_trips", {"from": "2000-01-01"})
    assert [t["start"] for t in trips["trips"]] == [trip.to_dict()["start"]]
    assert trips["open_trips"] == []
    old = await _ask(hass, "get_trips", {"to": "2000-01-31"})
    assert old == {"trips": [], "open_trips": []}

    summary = await _ask(hass, "get_driving_summary")
    assert summary["month"] == dt_util.now().strftime("%Y-%m")
    assert (await _ask(hass, "get_driving_summary", {"month": "2026-01"}))["month"] == "2026-01"
    efficiency = await _ask(hass, "get_efficiency")
    assert efficiency["vin"] == VIN
    assert "bmw_lifetime" in efficiency["efficiency"]
    assert (await _ask(hass, "get_mileage_report"))["vin"] == VIN
    assert (await _ask(hass, "get_fuel_history"))["vin"] == VIN
    assert "reports" in await _ask(hass, "get_coverage_report")

    evcc = await _ask(hass, "get_evcc_config")
    assert evcc["mqtt_available"] is False
    assert VIN in evcc["yaml"]


async def test_bad_arguments_are_refused_by_name(hass: HomeAssistant, no_bmw) -> None:
    await _account(hass)
    with pytest.raises(ServiceValidationError, match="month"):
        await _ask(hass, "get_driving_summary", {"month": "March"})
    with pytest.raises(ServiceValidationError, match="'from'"):
        await _ask(hass, "get_trips", {"from": "last week"})
    with pytest.raises(ServiceValidationError):
        await _ask(hass, "get_trips", {"vin": "WBASTRANGER000001"})


async def test_a_refusal_carries_what_the_frontend_translates(hass: HomeAssistant, no_bmw) -> None:
    """Home Assistant logs an error in English and the frontend shows it in the
    user's language, from the translation key and its placeholders."""

    await _account(hass)
    with pytest.raises(ServiceValidationError) as err:
        await _ask(hass, "get_driving_summary", {"month": "März"})

    assert (err.value.translation_domain, err.value.translation_key) == (DOMAIN, "month_format")
    assert err.value.translation_placeholders == {"value": "'März'"}
    assert str(err.value) == "'month' must be 'YYYY-MM', got 'März'"


async def test_the_month_exports_as_csv_and_html(hass: HomeAssistant, no_bmw) -> None:
    await _account(hass)
    month = dt_util.now().strftime("%Y-%m")

    csv = await _ask(hass, "export_history")
    assert [f["filename"] for f in csv["files"]] == [
        f"bavariandata-{VIN.lower()}-{month}-charging.csv",
        f"bavariandata-{VIN.lower()}-{month}-trips.csv",
    ]
    assert [f["rows"] for f in csv["files"]] == [1, 1]

    html = await _ask(hass, "export_history", {"format": "html", "type": "trips"})
    (page,) = html["files"]
    assert page["mime"] == "text/html" and page["rows"] == 1
    assert "month report" in page["content"]


# -- writing them -------------------------------------------------------------


async def test_a_trip_is_classified_by_its_id_alone(hass: HomeAssistant, no_bmw) -> None:
    _entry, runtime, trip = await _account(hass)

    await hass.services.async_call(
        DOMAIN,
        "set_trip_class",
        {"trip_id": trip.id, "classification": "business"},
        blocking=True,
    )
    assert runtime.history.find_trip(VIN, trip.id).classification == "business"

    with pytest.raises(ServiceValidationError, match="No trip"):
        await _call(hass, "set_trip_class", {"trip_id": f"{VIN}-nope", "classification": "private"})


async def test_mark_trip_start_needs_report_mode(hass: HomeAssistant, no_bmw) -> None:
    _entry, runtime, _trip = await _account(hass)
    with pytest.raises(ServiceValidationError, match="reports"):
        await _call(hass, "mark_trip_start")

    with (
        patch.object(runtime.coordinator, "report_trips", return_value=True),
        patch.object(runtime.coordinator, "mark_trip_start") as mark,
    ):
        await _call(hass, "mark_trip_start", {"at": "2026-10-24T08:00:00+00:00"})
    (vin, at), _ = mark.call_args
    assert vin == VIN and at.hour == 8


async def test_a_statistics_import_without_a_recorder_says_why(hass: HomeAssistant, no_bmw) -> None:
    _entry, runtime, _trip = await _account(hass)
    if runtime.statistics is None or not runtime.statistics.enabled:
        pytest.skip("statistics are off by default")
    with pytest.raises(ServiceValidationError, match="recorder"):
        await _call(hass, "import_statistics")


# -- asking BMW ---------------------------------------------------------------


@pytest.mark.parametrize(
    "service",
    [
        "fetch_basic_data",
        "fetch_charging_history",
        "fetch_tyre_diagnosis",
        "fetch_location_charging_settings",
        "fetch_telematic_data",
    ],
)
async def test_a_fetch_bmw_refuses_fails_the_call(
    hass: HomeAssistant, no_bmw, service: str
) -> None:
    await _account(hass)
    with pytest.raises(HomeAssistantError):
        await _call(hass, service)


async def test_a_spent_quota_fails_a_fetch(hass: HomeAssistant, no_bmw) -> None:
    from custom_components.bavariandata.rest import CardataQuotaError  # noqa: PLC0415

    _entry, runtime, _trip = await _account(hass)
    with (
        patch.object(runtime.rest, "claim", AsyncMock(side_effect=CardataQuotaError("spent"))),
        pytest.raises(HomeAssistantError, match="spent"),
    ):
        await _call(hass, "fetch_tyre_diagnosis")


async def test_bmw_answers_land_in_the_stores(hass: HomeAssistant, no_bmw) -> None:
    _entry, runtime, _trip = await _account(hass)

    async def bmw(_session, _method, path, *_args, **_kwargs):
        if path.endswith("/basicData"):
            return {"modelName": "i5 M60", "brand": "BMW"}
        if "chargingHistory" in path:
            return {"data": []}
        if "smartMaintenanceTyreDiagnosis" in path:
            return {}
        if "locationBasedChargingSettings" in path:
            return {"data": [{}]}
        return {}

    no_bmw["rest_json"].side_effect = bmw
    await _call(hass, "fetch_basic_data")
    assert runtime.coordinator.names.get(VIN) == "i5 M60"
    await _call(hass, "fetch_charging_history", {"from": "2026-10-01T00:00:00"})
    await _call(hass, "fetch_tyre_diagnosis")
    await _call(hass, "fetch_location_charging_settings")


async def test_mappings_adopt_a_new_car_and_flag_one_that_left(hass: HomeAssistant, no_bmw) -> None:
    entry, _runtime, _trip = await _account(hass)
    newcomer = "WBANEWCAR00000001"

    async def bmw(_session, _method, path, *_args, **_kwargs):
        if path.endswith("/mappings"):
            return [{"vin": newcomer, "mappingType": "PRIMARY"}]
        raise HomeAssistantError("only mappings here")

    no_bmw["rest_json"].side_effect = bmw
    with patch("custom_components.bavariandata.services._async_on_new_vehicle") as adopt:
        await hass.services.async_call(DOMAIN, "fetch_vehicle_mappings", {}, blocking=True)
    assert adopt.call_args.args[2] == newcomer
    issues = ir.async_get(hass).issues
    assert any(key[0] == DOMAIN and "left" in key[1] for key in issues)

    # Listed again: the repair goes.
    async def back(_session, _method, path, *_args, **_kwargs):
        return [
            {"vin": VIN, "mappingType": "PRIMARY"},
            {"vin": newcomer, "mappingType": "PRIMARY"},
        ]

    no_bmw["rest_json"].side_effect = back
    with patch("custom_components.bavariandata.services._async_on_new_vehicle"):
        await hass.services.async_call(
            DOMAIN, "fetch_vehicle_mappings", {"entry_id": entry.entry_id}, blocking=True
        )
    assert not any(key[0] == DOMAIN and "left" in key[1] for key in ir.async_get(hass).issues)


async def test_the_vehicle_image_service_asks_strictly(hass: HomeAssistant, no_bmw) -> None:
    await _account(hass)
    with patch(
        "custom_components.bavariandata.image.async_refresh_vehicle_image", AsyncMock()
    ) as refresh:
        await _call(hass, "fetch_vehicle_image")
    assert refresh.call_args.kwargs == {"strict": True}


# -- the portal ---------------------------------------------------------------

PORTAL = {
    "base_url": "https://portal.example",
    "locale": "de-de",
    "mapped_vehicle_id": "abc",
    "cookie": "c",
}


async def test_stream_fields_are_activated_through_the_portal(hass: HomeAssistant, no_bmw) -> None:
    await _account(hass)
    fields = ["a.b", "c.d", "e.f"]
    result = StreamActivationResult(mapped_vehicle_id="abc", requested=fields, accepted=fields)
    with patch(
        "custom_components.bavariandata.services.PortalStreamClient.async_set_stream_attributes",
        AsyncMock(return_value=result),
    ) as send:
        answer = await hass.services.async_call(
            DOMAIN,
            "activate_stream_fields",
            {**PORTAL, "attributes": ["a.b", "c.d", "e.f"]},
            blocking=True,
            return_response=True,
        )
    assert answer == {
        "mapped_vehicle_id": "abc",
        "requested": 3,
        "accepted": 3,
        "unchanged": False,
    }
    assert send.call_args.args == ("abc", ["a.b", "c.d", "e.f"])


@pytest.mark.parametrize(("kind", "said"), [("auth", "fresh session"), ("server", "BMW down")])
async def test_a_portal_refusal_says_what_to_do(
    hass: HomeAssistant, no_bmw, kind: str, said: str
) -> None:
    await _account(hass)
    with (
        patch(
            "custom_components.bavariandata.services.PortalStreamClient.async_set_stream_attributes",
            AsyncMock(side_effect=StreamActivationError("BMW down", kind=kind)),
        ),
        pytest.raises(ServiceValidationError, match=said),
    ):
        await hass.services.async_call(
            DOMAIN, "activate_stream_fields", PORTAL, blocking=True, return_response=True
        )


# -- the rest of the paths ----------------------------------------------------


async def test_a_car_is_picked_by_its_device(hass: HomeAssistant, no_bmw) -> None:
    from homeassistant.helpers import device_registry as dr  # noqa: PLC0415

    entry, _runtime, _trip = await _account(hass)
    registry = dr.async_get(hass)
    (car,) = [
        d
        for d in dr.async_entries_for_config_entry(registry, entry.entry_id)
        if (DOMAIN, VIN) in d.identifiers
    ]
    answer = await hass.services.async_call(
        DOMAIN, "get_mileage_report", {"device_id": car.id}, blocking=True, return_response=True
    )
    assert answer["vin"] == VIN

    stranger = registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("other", "x")}
    )
    with pytest.raises(ServiceValidationError, match="not a BavarianData car"):
        await hass.services.async_call(
            DOMAIN,
            "get_mileage_report",
            {"device_id": stranger.id},
            blocking=True,
            return_response=True,
        )


async def test_an_account_without_history_answers_empty(hass: HomeAssistant, no_bmw) -> None:
    _entry, runtime, _trip = await _account(hass)
    runtime.history = None
    runtime.coverage = None
    runtime.statistics = None

    assert await _ask(hass, "get_charging_sessions") == {"sessions": []}
    assert await _ask(hass, "get_charging_places") == {"places": [], "locations_remembered": False}
    assert await _ask(hass, "get_trips") == {"trips": [], "open_trips": []}
    assert await _ask(hass, "get_driving_summary") == {"summary": {}, "month": None}
    assert await _ask(hass, "get_efficiency") == {"efficiency": {}}
    assert await _ask(hass, "get_coverage_report") == {"reports": []}
    assert await _ask(hass, "export_history") == {"files": [], "month": None}
    assert await _ask(hass, "import_statistics") == {"statistics": {}}
    for service, data in (
        ("set_trip_class", {"trip_id": f"{VIN}-x", "classification": "private"}),
        ("mark_trip_start", {}),
    ):
        with pytest.raises(ServiceValidationError, match="not available"):
            await _call(hass, service, data)


async def test_a_drive_under_way_rides_along_with_the_trips(hass: HomeAssistant, no_bmw) -> None:
    _entry, runtime, _trip = await _account(hass)
    coordinator = runtime.coordinator
    progress = {"vin": VIN, "start": dt_util.utcnow().isoformat()}
    with (
        patch.object(coordinator, "open_trip_vins", return_value=[VIN, "WBAOTHER000000001"]),
        patch.object(coordinator, "open_trip_progress", return_value=progress),
    ):
        answer = await _ask(hass, "get_trips")
    assert answer["open_trips"] == [progress]


async def test_a_tariff_prices_the_driving_summary(hass: HomeAssistant, no_bmw) -> None:
    from custom_components.bavariandata.history.pricing import (  # noqa: PLC0415
        MODE_FIXED,
        PricingConfig,
    )

    _entry, runtime, _trip = await _account(hass)
    runtime.coordinator.pricing = PricingConfig(mode=MODE_FIXED, fixed_price=0.3)
    summary = await _ask(hass, "get_driving_summary")
    assert summary["summary"] is not None


async def test_statistics_import_states_why_it_cannot(hass: HomeAssistant, no_bmw) -> None:
    from types import SimpleNamespace  # noqa: PLC0415

    _entry, runtime, _trip = await _account(hass)
    publish = AsyncMock(return_value={"rows": 2})
    runtime.statistics = SimpleNamespace(enabled=False, available=True, async_publish=publish)
    with pytest.raises(ServiceValidationError, match="turned off"):
        await _call(hass, "import_statistics")
    runtime.statistics.enabled = True
    runtime.statistics.available = False
    with pytest.raises(ServiceValidationError, match="recorder"):
        await _call(hass, "import_statistics")
    runtime.statistics.available = True
    assert await _ask(hass, "import_statistics") == {"statistics": {"rows": 2}}
    assert publish.call_args.kwargs == {"vin": VIN, "force": True}


async def test_a_fetch_without_a_token_asks_for_reauthorization(
    hass: HomeAssistant, no_bmw
) -> None:
    from custom_components.bavariandata.device_flow import CardataAuthError  # noqa: PLC0415

    _entry, runtime, _trip = await _account(hass)
    with (
        patch.object(runtime.tokens, "access_token", AsyncMock(side_effect=CardataAuthError("x"))),
        pytest.raises(HomeAssistantError, match="reauthorize"),
    ):
        await _call(hass, "fetch_tyre_diagnosis")


async def test_failed_mappings_and_a_good_telematic_fetch(hass: HomeAssistant, no_bmw) -> None:
    entry, _runtime, _trip = await _account(hass)
    with pytest.raises(HomeAssistantError, match="fetch_vehicle_mappings"):
        await hass.services.async_call(DOMAIN, "fetch_vehicle_mappings", {}, blocking=True)

    with (
        patch(
            "custom_components.bavariandata.services._async_perform_telematic_fetch",
            AsyncMock(return_value=True),
        ),
        patch("custom_components.bavariandata.services._async_update_last_telematic_poll") as noted,
    ):
        await _call(hass, "fetch_telematic_data")
    assert noted.call_args.args[1] is entry


async def test_portal_activation_needs_something_to_activate(hass: HomeAssistant, no_bmw) -> None:
    await _account(hass)
    with pytest.raises(ServiceValidationError, match="No attributes"):
        await hass.services.async_call(
            DOMAIN,
            "activate_stream_fields",
            {**PORTAL, "sections": ["no-such-cluster"]},
            blocking=True,
            return_response=True,
        )
