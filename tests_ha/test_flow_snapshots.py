"""Every setup and Configure screen, walked and pinned.

The baseline for splitting ``config_flow.py``: each test walks one path
through Home Assistant's own flow manager and snapshots every step the way
the frontend receives it (``_prepare_result_json``, the flow views' own
serializer) -- step, form fields with their defaults and prefills, errors,
placeholders, menus, the button label (``last_step``), and what a finished
flow saves. A refactor that changes any screen shows up as a diff in
``snapshots/test_flow_snapshots.ambr``; an intended change is approved with
``bash scripts/test_ha.sh -k flow_snapshots --snapshot-update`` and the diff
read.

Option keys are written out, not imported: they are stored in every user's
entry, so a renamed constant must fail here.
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.core_config import async_process_ha_core_config
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.data_entry_flow import _BaseFlowManagerView
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.bavariandata.const import DOMAIN
from custom_components.bavariandata.container import CardataContainerManager
from custom_components.bavariandata.device_flow import CardataAuthError

from .conftest import PACKAGE, VIN, make_entry, set_up
from .test_config_flow import CLIENT_ID, DEVICE, FLOW, TOKENS, _Poll

VIN_B = "WBATEST0000000002"
SOC = "vehicle.drivetrain.batteryManagement.header"
_TOKEN = re.compile(r"token=[A-Za-z0-9_-]+")
# What a step shows; the rest (flow id, handler, the progress task) is plumbing.
_SHOWN = (
    "type",
    "step_id",
    "menu_options",
    "data_schema",
    "errors",
    "description_placeholders",
    "last_step",
    "progress_action",
    "reason",
    "title",
    "data",
)


def _result_blob(**data: Any) -> str:
    """What the in-browser activator hands back, by webhook or paste."""

    payload = {"v": 1, "origin": "https://www.bmw.de", "locale": "de-DE", **data}
    return "BAVARIANDATA1:" + base64.b64encode(json.dumps(payload).encode()).decode()


ACTIVATED = _result_blob(
    mappedVehicleId="abcdef1234567890",
    clientIds=[{"apikey": CLIENT_ID, "scopes": ["cardata:api:read", "cardata:streaming:read"]}],
    activated={"ok": True, "total": 42, "added": 12},
)
NO_CLIENT = _result_blob(mappedVehicleId="abcdef1234567890", clientIds=[])


class _Walk:
    """The steps of one or more flows, as the frontend saw them."""

    def __init__(self) -> None:
        self.steps: list[Any] = []

    def __call__(self, result: dict) -> dict:
        self.steps.append(_shape(result))
        return result

    def note(self, label: str, value: Any = None) -> None:
        """A marker between flows, or something the screens left behind."""

        self.steps.append(label if value is None else {label: value})


def _shape(result: dict) -> dict:
    if result["type"] is FlowResultType.CREATE_ENTRY:
        shown = dict(result)  # the view drops ``data``; what is saved is the point
    else:
        shown = _BaseFlowManagerView._prepare_result_json(None, result)
    shown = {key: shown[key] for key in _SHOWN if key in shown}
    shown["type"] = shown["type"].value
    if isinstance(shown.get("data"), dict) and "received_at" in shown["data"]:
        shown["data"] = {**shown["data"], "received_at": "<time>"}
    # The one-time token in the activation link differs on every run.
    return json.loads(_TOKEN.sub("token=<token>", json.dumps(shown, default=str)))


def _token(result: dict) -> str:
    match = _TOKEN.search(result["description_placeholders"]["link_open"])
    assert match, "the step links the activation page"
    return match.group(0).removeprefix("token=")


@pytest.fixture
def device_flow():
    """BMW's device flow, answered: a device code, and a poll the test releases."""

    poll = _Poll()
    with (
        patch(f"{FLOW}.request_device_code", return_value=DEVICE) as device_code,
        patch(f"{FLOW}.poll_for_tokens", poll),
    ):
        poll.device_code = device_code
        yield poll


@pytest.fixture
def bmw_auth(device_flow):
    """The device flow, for a setup whose entry is made but not set up."""

    with patch(f"{PACKAGE}.async_setup_entry", return_value=True):
        yield device_flow


@pytest.fixture
async def https(hass: HomeAssistant) -> None:
    """An instance reachable over https, so the activator can report back."""

    await async_process_ha_core_config(hass, {"external_url": "https://ha.example.com"})
    assert await async_setup_component(hass, "webhook", {})
    # What Home Assistant's own server start does: a flow registers the helper
    # page's view long after startup, and the test client would freeze the router.
    hass.http.app._router.freeze = lambda: None


async def _released(hass: HomeAssistant, poll: _Poll) -> None:
    poll.released.set()
    await hass.async_block_till_done()


# --- First-time setup ---------------------------------------------------------


async def _start(hass: HomeAssistant, walk: _Walk, path: str, *, menu: bool = False) -> dict:
    """Open setup and pick ``path``; the menu itself is pinned once."""

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    if menu:
        walk(result)
    return await _step(hass, walk, result, {"next_step_id": path})


async def _step(hass: HomeAssistant, walk: _Walk, result: dict, user_input=None) -> dict:
    return walk(await hass.config_entries.flow.async_configure(result["flow_id"], user_input))


async def test_manual_setup(hass: HomeAssistant, bmw_auth, snapshot) -> None:
    walk = _Walk()
    result = await _start(hass, walk, "manual", menu=True)
    bmw_auth.device_code.side_effect = CardataAuthError("refused")
    result = await _step(hass, walk, result, {"client_id": CLIENT_ID})
    bmw_auth.device_code.side_effect = None
    result = await _step(hass, walk, result, {"client_id": f" {CLIENT_ID} "})
    await _released(hass, bmw_auth)
    result = await _step(hass, walk, result)
    await _step(hass, walk, result, {"sections": []})

    assert walk.steps == snapshot


async def test_manual_setup_activates_by_paste(hass: HomeAssistant, bmw_auth, snapshot) -> None:
    """Over http the activator cannot report back; its result is pasted."""

    walk = _Walk()
    for paste in ("", ACTIVATED):
        walk.note(f"paste {'nothing' if not paste else 'the result'}")
        bmw_auth.released.clear()
        result = await _start(hass, walk, "manual")
        result = await _step(hass, walk, result, {"client_id": CLIENT_ID})
        await _released(hass, bmw_auth)
        result = await _step(hass, walk, result)
        result = await _step(hass, walk, result, {"sections": ["electric", "status"]})
        if paste:
            result = await _step(hass, walk, result, {"result": "not a result"})
            result = await _step(hass, walk, result, {"result": paste})
            result = await _step(hass, walk, result, {})
        else:
            result = await _step(hass, walk, result, {"result": paste})
        for entry in hass.config_entries.async_entries(DOMAIN):
            await hass.config_entries.async_remove(entry.entry_id)

    assert walk.steps == snapshot


async def test_manual_setup_activates_by_webhook(
    hass: HomeAssistant, https, bmw_auth, hass_client_no_auth, snapshot
) -> None:
    walk = _Walk()
    client = await hass_client_no_auth()
    result = await _start(hass, walk, "manual")
    result = await _step(hass, walk, result, {"client_id": CLIENT_ID})
    await _released(hass, bmw_auth)
    result = await _step(hass, walk, result)
    result = await _step(hass, walk, result, {"sections": ["electric"]})
    token = _token(result)

    page = await client.get(f"/{DOMAIN}/onboarding", params={"token": token})
    walk.note("helper page", page.status)
    walk.note("bad report", (await client.post(f"/api/webhook/{token}", data="nonsense")).status)
    walk.note("report", (await client.post(f"/api/webhook/{token}", data=ACTIVATED)).status)
    await hass.async_block_till_done()
    result = await _step(hass, walk, result)
    await _step(hass, walk, result, {})
    page = await client.get(f"/{DOMAIN}/onboarding", params={"token": token})
    walk.note("helper page after setup", page.status)

    assert walk.steps == snapshot


async def test_a_refused_authorization_is_retried(hass: HomeAssistant, bmw_auth, snapshot) -> None:
    walk = _Walk()
    bmw_auth.answer = CardataAuthError(
        "denied",
        status=400,
        error_code="access_denied",
        error_description="The user denied",
        correlation_id="corr-1",
    )
    result = await _start(hass, walk, "manual")
    result = await _step(hass, walk, result, {"client_id": CLIENT_ID})
    await _released(hass, bmw_auth)
    result = await _step(hass, walk, result)

    bmw_auth.device_code.side_effect = CardataAuthError("still refused")
    result = await _step(hass, walk, result, {})
    bmw_auth.device_code.side_effect = None
    bmw_auth.answer = TOKENS
    bmw_auth.released.clear()
    result = await _step(hass, walk, result, {})
    await _released(hass, bmw_auth)
    await _step(hass, walk, result)

    assert walk.steps == snapshot


async def test_an_account_already_set_up(hass: HomeAssistant, bmw_auth, snapshot) -> None:
    """Another client of the same account is refused; the same client re-authorizes."""

    walk = _Walk()
    other = MockConfigEntry(
        domain=DOMAIN, unique_id="another-client", data={"client_id": "x", "gcid": "gcid-1"}
    )
    other.add_to_hass(hass)
    walk.note("another client of the same account")
    result = await _start(hass, walk, "manual")
    result = await _step(hass, walk, result, {"client_id": CLIENT_ID})
    await _released(hass, bmw_auth)
    await _step(hass, walk, result)

    walk.note("the same client again")
    same = MockConfigEntry(
        domain=DOMAIN,
        unique_id=CLIENT_ID,
        data={"client_id": CLIENT_ID, "gcid": "gcid-1", "refresh_token": "old", "keep": True},
    )
    await hass.config_entries.async_remove(other.entry_id)
    same.add_to_hass(hass)
    bmw_auth.released.clear()
    result = await _start(hass, walk, "manual")
    result = await _step(hass, walk, result, {"client_id": CLIENT_ID})
    await _released(hass, bmw_auth)
    await _step(hass, walk, result)
    walk.note("entry data", {**same.data, "received_at": "<time>"})

    assert walk.steps == snapshot


async def test_guided_setup_by_paste(hass: HomeAssistant, bmw_auth, snapshot) -> None:
    walk = _Walk()
    result = await _start(hass, walk, "guided")
    result = await _step(hass, walk, result, {"result": "not a result"})
    result = await _step(hass, walk, result, {"result": NO_CLIENT})
    bmw_auth.device_code.side_effect = CardataAuthError("refused")
    result = await _step(hass, walk, result, {"result": ACTIVATED})
    bmw_auth.device_code.side_effect = None
    result = await _step(hass, walk, result, {"result": ACTIVATED})
    await _released(hass, bmw_auth)
    result = await _step(hass, walk, result)
    await _step(hass, walk, result, {})

    assert walk.steps == snapshot


async def test_guided_setup_by_webhook(
    hass: HomeAssistant, https, bmw_auth, hass_client_no_auth, snapshot
) -> None:
    walk = _Walk()
    client = await hass_client_no_auth()

    walk.note("the activator reports")
    result = await _start(hass, walk, "guided")
    token = _token(result)
    page = await client.get(f"/{DOMAIN}/onboarding", params={"token": token})
    walk.note("helper page", page.status)
    walk.note("report", (await client.post(f"/api/webhook/{token}", data=ACTIVATED)).status)
    await hass.async_block_till_done()
    result = await _step(hass, walk, result)
    await _released(hass, bmw_auth)
    result = await _step(hass, walk, result)
    await _step(hass, walk, result, {})

    walk.note("a report without a client id")
    await hass.config_entries.async_remove(hass.config_entries.async_entries(DOMAIN)[0].entry_id)
    result = await _start(hass, walk, "guided")
    await client.post(f"/api/webhook/{_token(result)}", data=NO_CLIENT)
    await hass.async_block_till_done()
    await _step(hass, walk, result)

    walk.note("no report in time")
    with patch(f"{FLOW}.ONBOARDING_WAIT_TIMEOUT", 0):
        result = await _start(hass, walk, "guided")
        await hass.async_block_till_done()
        await _step(hass, walk, result)

    assert walk.steps == snapshot


async def test_reauthentication(hass: HomeAssistant, bmw_auth, snapshot) -> None:
    walk = _Walk()
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=CLIENT_ID,
        data={"client_id": CLIENT_ID, "gcid": "gcid-1", "reauth_pending": True},
    )
    entry.add_to_hass(hass)

    async def _reauth(data: dict) -> dict:
        return walk(
            await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
                data=data,
            )
        )

    walk.note("no client id")
    await _reauth({"entry_id": entry.entry_id})
    walk.note("BMW refuses a device code")
    bmw_auth.device_code.side_effect = CardataAuthError("refused")
    await _reauth({"entry_id": entry.entry_id, "client_id": CLIENT_ID})
    bmw_auth.device_code.side_effect = None
    walk.note("authorized")
    result = await _reauth({"entry_id": entry.entry_id, "client_id": CLIENT_ID})
    await _released(hass, bmw_auth)
    await _step(hass, walk, result)
    walk.note("entry data", {**entry.data, "received_at": "<time>"})

    assert walk.steps == snapshot


# --- Configure ----------------------------------------------------------------


async def _account(hass: HomeAssistant, *vins: str, **data: Any) -> MockConfigEntry:
    """A running account whose cars have streamed once."""

    names = {VIN: "i5 eDrive40", VIN_B: "iX1 xDrive30"}
    entry = make_entry(
        vehicle_metadata={vin: {"modelName": names[vin], "brand": "BMW"} for vin in vins}, **data
    )
    await set_up(hass, entry)
    for vin in vins:
        await entry.runtime_data.coordinator.async_handle_message(
            {
                "vin": vin,
                "data": {SOC: {"value": 63, "unit": "%", "timestamp": "2026-10-24T08:00:00Z"}},
            }
        )
    await hass.async_block_till_done()
    return entry


async def _configure(hass: HomeAssistant, entry: MockConfigEntry, walk: _Walk, *path: str) -> dict:
    """Open Configure and follow ``path`` through the menus (not recorded)."""

    result = await hass.config_entries.options.async_init(entry.entry_id)
    for step in path[:-1]:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": step}
        )
    return await _submit(hass, walk, result, {"next_step_id": path[-1]})


async def _submit(hass: HomeAssistant, walk: _Walk, result: dict, user_input=None) -> dict:
    return walk(await hass.config_entries.options.async_configure(result["flow_id"], user_input))


async def test_configure_menus(hass: HomeAssistant, no_bmw, snapshot) -> None:
    walk = _Walk()
    entry = await _account(hass, VIN)
    walk(await hass.config_entries.options.async_init(entry.entry_id))
    await _configure(hass, entry, walk, "fetch_now")
    await _configure(hass, entry, walk, "connection")

    assert walk.steps == snapshot


async def test_configure_charging_costs(hass: HomeAssistant, no_bmw, snapshot) -> None:
    walk = _Walk()
    entry = await _account(hass, VIN)
    shared = {
        "price_currency": "EUR",
        "charging_loss_percent": 0,
        "history_retain_months": 24,
        "statistics_import": False,
        "charge_locations": False,
    }
    result = await _configure(hass, entry, walk, "action_charging_costs")
    result = await _submit(hass, walk, result, {**shared, "price_mode": "fixed"})
    result = await _submit(hass, walk, result, {**shared, "price_mode": "entity"})
    await _submit(
        hass,
        walk,
        result,
        {**shared, "price_mode": "fixed", "price_fixed": 0.32, "charging_loss_percent": 7.5},
    )
    await _configure(hass, entry, walk, "action_charging_costs")

    assert walk.steps == snapshot


async def test_configure_energy_sources(hass: HomeAssistant, no_bmw, snapshot) -> None:
    walk = _Walk()
    entry = await _account(hass, VIN)
    result = await _configure(hass, entry, walk, "action_energy_sources")
    result = await _submit(
        hass, walk, result, {"pv_power_entity": "sensor.pv", "battery_power_invert": False}
    )
    await _submit(
        hass,
        walk,
        result,
        {
            "pv_power_entity": "sensor.pv",
            "grid_power_entity": "sensor.grid",
            "battery_power_invert": True,
            "price_solar": 0.08,
        },
    )

    assert walk.steps == snapshot


async def test_configure_evcc_bridge(hass: HomeAssistant, no_bmw, snapshot) -> None:
    walk = _Walk()
    entry = await _account(hass, VIN)
    result = await _configure(hass, entry, walk, "action_evcc_bridge")
    result = await _submit(hass, walk, result, {"bridge_enabled": True, "bridge_retain": True})
    result = await _submit(hass, walk, result, {"bridge_enabled": False, "bridge_retain": True})
    await _submit(hass, walk, result, {})

    walk.note("with MQTT set up")
    MockConfigEntry(domain="mqtt").add_to_hass(hass)
    with patch(f"{PACKAGE}.bridge.VehicleBridge.async_publish_all", new_callable=AsyncMock):
        result = await _configure(hass, entry, walk, "action_evcc_bridge")
        result = await _submit(
            hass,
            walk,
            result,
            {"bridge_enabled": True, "bridge_topic_prefix": "cars", "bridge_retain": False},
        )
        await _submit(hass, walk, result, {})

    assert walk.steps == snapshot


TRIPS = {
    "trip_default_class": "private",
    "trip_commute_gap": 15,
    "trip_detection": "live",
    "trip_track": False,
    "trip_geocode": False,
    "trip_debug": False,
}


async def test_configure_trips(hass: HomeAssistant, no_bmw, snapshot) -> None:
    walk = _Walk()
    entry = await _account(hass, VIN)
    result = await _configure(hass, entry, walk, "action_trips")
    await _submit(
        hass, walk, result, {**TRIPS, "trip_work_zone": "zone.work", "trip_geocode": True}
    )

    walk.note("a car on end-of-trip reports that records its route")
    result = await _configure(hass, entry, walk, "action_trips")
    result = await _submit(
        hass, walk, result, {**TRIPS, "trip_detection": "reports", "trip_track": True}
    )
    result = await _submit(hass, walk, result, {"trip_phone_in_car": "binary_sensor.phone_in_car"})
    await _submit(
        hass,
        walk,
        result,
        {
            "trip_phone_tracker": "device_tracker.phone",
            "trip_phone_in_car": "binary_sensor.phone_in_car",
        },
    )
    await _configure(hass, entry, walk, "action_trips")

    assert walk.steps == snapshot


async def test_configure_mileage(hass: HomeAssistant, no_bmw, freezer, snapshot) -> None:
    freezer.move_to("2026-10-24T10:00:00+00:00")
    walk = _Walk()
    entry = await _account(hass, VIN)

    walk.note("a lease")
    result = await _configure(hass, entry, walk, "action_mileage")
    result = await _submit(hass, walk, result, {"mileage_kind": "lease"})
    lease = {"mileage_start": "2026-11-01", "mileage_end": "2026-10-01", "mileage_start_km": 1200}
    result = await _submit(hass, walk, result, {**lease, "mileage_allowance_km": 0})
    result = await _submit(hass, walk, result, {**lease, "mileage_allowance_km": 15000})
    await _submit(
        hass,
        walk,
        result,
        {**lease, "mileage_end": "2029-10-31", "mileage_allowance_km": 15000},
    )

    walk.note("a yearly limit")
    result = await _configure(hass, entry, walk, "action_mileage")
    result = await _submit(hass, walk, result, {"mileage_kind": "yearly"})
    await _submit(
        hass,
        walk,
        result,
        {"mileage_start": "2025-03-01", "mileage_allowance_km": 12000, "mileage_start_km": 900},
    )

    walk.note("none")
    result = await _configure(hass, entry, walk, "action_mileage")
    await _submit(hass, walk, result, {"mileage_kind": "none"})

    assert walk.steps == snapshot


async def test_configure_units_refresh_and_debug(hass: HomeAssistant, no_bmw, snapshot) -> None:
    walk = _Walk()
    entry = await _account(hass, VIN)
    result = await _configure(hass, entry, walk, "action_units")
    await _submit(
        hass,
        walk,
        result,
        {
            "unit_distance": "mi",
            "unit_temperature": "f",
            "unit_pressure": "psi",
        },
    )
    result = await _configure(hass, entry, walk, "action_data_refresh")
    await _submit(hass, walk, result, {"refresh_on_start": False})
    result = await _configure(hass, entry, walk, "connection", "action_debug_logging")
    await _submit(hass, walk, result, {"debug_log": True})

    assert walk.steps == snapshot


async def test_configure_streamed_data(hass: HomeAssistant, no_bmw, snapshot) -> None:
    walk = _Walk()
    entry = await _account(hass, VIN)

    walk.note("nothing chosen")
    result = await _configure(hass, entry, walk, "action_select_clusters")
    await _submit(hass, walk, result, {"sections": []})
    walk.note("entry sections", entry.data["stream_sections"])

    walk.note("pasted result")
    result = await _configure(hass, entry, walk, "action_select_clusters")
    result = await _submit(hass, walk, result, {"sections": ["electric"]})
    result = await _submit(hass, walk, result, {"result": "not a result"})
    result = await _submit(hass, walk, result, {"result": ACTIVATED})
    await _submit(hass, walk, result, {})
    walk.note("entry sections", entry.data["stream_sections"])

    assert walk.steps == snapshot


async def test_configure_streamed_data_by_webhook(
    hass: HomeAssistant, https, no_bmw, hass_client_no_auth, snapshot
) -> None:
    walk = _Walk()
    entry = await _account(hass, VIN)
    client = await hass_client_no_auth()
    result = await _configure(hass, entry, walk, "action_select_clusters")
    result = await _submit(hass, walk, result, {"sections": ["electric", "status"]})
    walk.note(
        "report",
        (await client.post(f"/api/webhook/{_token(result)}", data=ACTIVATED)).status,
    )
    await hass.async_block_till_done()
    result = await _submit(hass, walk, result)
    await _submit(hass, walk, result, {})

    assert walk.steps == snapshot


async def test_configure_picture(hass: HomeAssistant, no_bmw, snapshot) -> None:
    walk = _Walk()
    entry = await _account(hass, VIN)
    result = await _configure(hass, entry, walk, "action_fetch_image")
    result = await _submit(hass, walk, result, {"image_source": "official"})
    with patch(
        f"{PACKAGE}.image.async_refresh_configurator_render",
        AsyncMock(return_value="unsupported"),
    ):
        await _submit(hass, walk, result, {"image_source": "configurator"})

    assert walk.steps == snapshot


@pytest.mark.parametrize(
    "action",
    [
        "action_fetch_mappings",
        "action_fetch_telematic",
        "action_fetch_basic",
        "action_fetch_charging_history",
        "action_fetch_tyre",
        "action_fetch_location_charging",
    ],
)
async def test_configure_fetch_now(hass: HomeAssistant, no_bmw, action, snapshot) -> None:
    """Each fetch asks first; BMW unreachable comes back on the form."""

    walk = _Walk()
    entry = await _account(hass, VIN)
    result = await _configure(hass, entry, walk, "fetch_now", action)
    await _submit(hass, walk, result, {})

    assert walk.steps == snapshot


async def test_configure_connection(hass: HomeAssistant, no_bmw, device_flow, snapshot) -> None:
    walk = _Walk()
    entry = await _account(hass, VIN)
    runtime = entry.runtime_data

    walk.note("renew the token")
    result = await _configure(hass, entry, walk, "connection", "action_refresh_tokens")
    with patch(f"{FLOW}.async_manual_refresh_tokens", side_effect=CardataAuthError("expired")):
        result = await _submit(hass, walk, result, {})
    with patch(f"{FLOW}.async_manual_refresh_tokens"):
        await _submit(hass, walk, result, {})

    walk.note("reset the containers")
    result = await _configure(hass, entry, walk, "connection", "action_reset_container")
    with patch.object(
        runtime.tokens, "access_token", AsyncMock(side_effect=CardataAuthError("no token"))
    ):
        result = await _submit(hass, walk, result, {})
    with (
        patch.object(runtime.tokens, "access_token", AsyncMock(return_value="access")),
        patch.object(
            CardataContainerManager,
            "async_reset_hv_container",
            AsyncMock(return_value="container-new"),
        ),
    ):
        await _submit(hass, walk, result, {})
    walk.note(
        "entry containers",
        {key: value for key, value in entry.data.items() if "container" in key},
    )

    walk.note("authorize again")
    result = await _configure(hass, entry, walk, "connection", "action_reauth")
    result = await _submit(hass, walk, result, {"client_id": "  "})
    with patch(f"{PACKAGE}.stream.CardataStreamManager.async_update_credentials"):
        await _submit(hass, walk, result, {"client_id": f" {CLIENT_ID} "})
        (flow,) = hass.config_entries.flow.async_progress()
        walk.note("reauth flow", flow["step_id"])
        await _released(hass, device_flow)
        walk(await hass.config_entries.flow.async_configure(flow["flow_id"]))
    walk.note("client id", entry.data["client_id"])

    assert walk.steps == snapshot


async def test_configure_two_cars(hass: HomeAssistant, no_bmw, freezer, snapshot) -> None:
    """With two cars, per-car screens pick the car first and share the rest."""

    freezer.move_to("2026-10-24T10:00:00+00:00")
    walk = _Walk()
    entry = await _account(hass, VIN, VIN_B)

    walk.note("trips")
    result = await _configure(hass, entry, walk, "action_trips")
    result = await _submit(hass, walk, result, {"vehicle": VIN_B})
    car = {k: v for k, v in TRIPS.items() if k not in ("trip_geocode", "trip_debug")}
    await _submit(
        hass,
        walk,
        result,
        {
            **car,
            "trip_default_class": "business",
            "all_vehicles": {"trip_geocode": True, "trip_debug": False},
        },
    )

    walk.note("charging costs")
    result = await _configure(hass, entry, walk, "action_charging_costs")
    result = await _submit(hass, walk, result, {"vehicle": VIN})
    await _submit(
        hass,
        walk,
        result,
        {
            "grid_energy_entity": "sensor.wallbox_energy",
            "charging_loss_percent": 5,
            "all_vehicles": {
                "price_mode": "none",
                "price_currency": "EUR",
                "history_retain_months": 0,
                "statistics_import": False,
                "charge_locations": False,
            },
        },
    )

    walk.note("evcc bridge")
    result = await _configure(hass, entry, walk, "action_evcc_bridge")
    await _submit(hass, walk, result, {"vehicle": VIN_B})

    walk.note("mileage")
    result = await _configure(hass, entry, walk, "action_mileage")
    result = await _submit(hass, walk, result, {"vehicle": VIN_B})
    await _submit(hass, walk, result, {"mileage_kind": "yearly"})

    walk.note("units")
    await _configure(hass, entry, walk, "action_units")

    assert walk.steps == snapshot


async def test_configure_without_a_running_account(hass: HomeAssistant, no_bmw, snapshot) -> None:
    """An entry that is not loaded: fetches say so instead of failing."""

    walk = _Walk()
    entry = make_entry()
    entry.add_to_hass(hass)
    for path in (
        ("fetch_now", "action_fetch_basic"),
        ("fetch_now", "action_fetch_tyre"),
        ("action_fetch_image",),
        ("connection", "action_reset_container"),
        ("action_trips",),
        ("action_units",),
    ):
        await _configure(hass, entry, walk, *path)
    result = await _configure(hass, entry, walk, "action_mileage")
    await _submit(hass, walk, result, {"mileage_kind": "none"})

    assert walk.steps == snapshot
