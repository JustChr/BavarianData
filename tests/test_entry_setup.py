"""What setting up an account decides, without Home Assistant.

``async_setup_entry`` makes the calls; ``entry_setup`` decides them: how the
options configure the coordinator, the history's one-time repairs and their
order, which stored cars come back as devices, and which registry entries are
ours to re-enable or remove.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from .conftest import load_module

setup = load_module("entry_setup")
const = load_module("const")
metadata = load_module("descriptor_metadata").DESCRIPTOR_META

VIN = "WBAEXAMPLE0000001"
HEADER = "vehicle.drivetrain.batteryManagement.header"


def row(entity_id: str, unique_id: str, disabled_by=None) -> SimpleNamespace:
    return SimpleNamespace(entity_id=entity_id, unique_id=unique_id, disabled_by=disabled_by)


# -- options ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({}, const.DEBUG_LOG),
        ({const.OPTION_DEBUG_LOG: True}, True),
        ({const.OPTION_DEBUG_LOG: 0}, False),
    ],
)
def test_debug_logging_follows_the_option_or_the_build_default(options, expected) -> None:
    assert setup.debug_flag(options) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(6, 6), ("12", 12), (0, None), (-3, None), ("never", const.DEFAULT_HISTORY_RETAIN_MONTHS)],
)
def test_retention_reads_zero_as_forever_and_nonsense_as_the_default(value, expected) -> None:
    assert setup.retain_months({const.OPTION_HISTORY_RETAIN_MONTHS: value}) == expected


def test_retention_unset_is_the_default() -> None:
    assert setup.retain_months({}) == const.DEFAULT_HISTORY_RETAIN_MONTHS


def test_configure_hands_the_coordinator_every_option() -> None:
    coordinator = SimpleNamespace()
    session = object()
    setup.configure(
        coordinator,
        {
            const.OPTION_TRIP_GEOCODE: True,
            const.OPTION_TRIP_DEBUG: 1,
            const.OPTION_DIAGNOSTIC_INTERVAL: 42,
        },
        session,
    )

    assert coordinator.geocoder.enabled is True
    assert coordinator.geocoder._session is session
    assert coordinator.trip_debug is True
    assert coordinator.diagnostic_interval == 42
    assert coordinator.vehicle_pricing == {}
    for name in ("pricing", "trip_settings", "vehicle_trip_settings", "mileage_allowances"):
        assert hasattr(coordinator, name), name
    assert hasattr(coordinator, "display_units")


def test_configure_without_options_leaves_the_opt_ins_off() -> None:
    coordinator = SimpleNamespace()
    setup.configure(coordinator, {}, None)

    assert coordinator.geocoder.enabled is False
    assert coordinator.trip_debug is False
    assert coordinator.diagnostic_interval == const.DIAGNOSTIC_LOG_INTERVAL


# -- entry data -----------------------------------------------------------------


def test_an_entry_without_retired_keys_is_left_alone() -> None:
    assert setup.without_retired_keys({"client_id": "c", "gcid": "g"}) is None


def test_retired_keys_are_dropped_and_the_rest_kept() -> None:
    data = {"client_id": "c", "base_container_id": "old", "rest_base_vins": [VIN]}

    assert setup.without_retired_keys(data) == {"client_id": "c"}
    assert "base_container_id" in data, "the entry's own data is not changed in place"


# -- history repairs ------------------------------------------------------------


class History:
    def __init__(self, *, remember_locations: bool) -> None:
        self.remember_locations = remember_locations
        self.calls: list[str] = []

    def reresolve_zones(self, zone_at) -> None:
        self.calls.append(f"reresolve_zones:{zone_at()}")

    def drop_house_figures_elsewhere(self) -> None:
        self.calls.append("drop_house_figures_elsewhere")

    def flag_unbilled_meter_energy(self) -> None:
        self.calls.append("flag_unbilled_meter_energy")

    def forget_locations(self) -> None:
        self.calls.append("forget_locations")


def test_history_repairs_run_in_order_and_forget_locations_last() -> None:
    """The zone repair needs the coordinates, and the house-figure repair the zones."""

    history = History(remember_locations=False)
    setup.repair_history(history, lambda: "home")

    assert history.calls == [
        "reresolve_zones:home",
        "drop_house_figures_elsewhere",
        "flag_unbilled_meter_energy",
        "forget_locations",
    ]


def test_remembered_locations_stay() -> None:
    history = History(remember_locations=True)
    setup.repair_history(history, lambda: None)

    assert "forget_locations" not in history.calls


# -- stored cars ----------------------------------------------------------------


class Coordinator:
    def __init__(self, answers: dict) -> None:
        self.answers = answers
        self.applied: list[str] = []

    def apply_basic_data(self, vin: str, payload: dict):
        self.applied.append(vin)
        answer = self.answers[vin]
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_stored_cars_come_back_as_devices() -> None:
    coordinator = Coordinator({VIN: {"name": "i5", "model": "i5 eDrive40", "manufacturer": "BMW"}})

    devices = setup.restore_metadata(coordinator, {VIN: {"brand": "BMW"}})

    assert devices == [
        (
            VIN,
            {
                "manufacturer": "BMW",
                "name": "i5",
                "model": "i5 eDrive40",
                "sw_version": None,
                "hw_version": None,
                "serial_number": None,
            },
        )
    ]


def test_a_device_without_a_name_is_named_after_its_vin() -> None:
    assert setup.device_fields(VIN, {})["name"] == VIN
    assert setup.device_fields(VIN, {})["manufacturer"] == "BMW"


def test_a_damaged_stored_car_is_skipped_not_fatal() -> None:
    other = "WBAEXAMPLE0000002"
    third = "WBAEXAMPLE0000003"
    coordinator = Coordinator({VIN: ValueError("bad"), other: {}, third: {"name": "iX"}})

    devices = setup.restore_metadata(
        coordinator, {VIN: {}, other: {}, third: {}, "WBAEXAMPLE0000004": "not a dict"}
    )

    assert [vin for vin, _ in devices] == [third]
    assert coordinator.applied == [VIN, other, third]


@pytest.mark.parametrize("stored", [None, [], "nonsense"])
def test_no_stored_cars_means_no_devices(stored) -> None:
    assert setup.restore_metadata(Coordinator({}), stored) == []


# -- the registry ---------------------------------------------------------------


def test_registry_fixes_re_enable_first_then_remove_each_entity_once() -> None:
    petrol = "WBAEXAMPLE0000002"
    rows = [
        row("sensor.i5_header", f"{VIN}_{HEADER}", disabled_by="integration"),
        row("sensor.i5_testing", f"{VIN}_soc_estimate_testing"),
        row("sensor.m240_real_range", f"{petrol}_real_range"),
        row("sensor.m240_soc_estimate", f"{petrol}_soc_estimate"),
        row("sensor.i5_header_user", f"{VIN}_{HEADER}", disabled_by="user"),
    ]

    fixes = setup.registry_fixes(
        rows,
        metadata,
        combustion_only=lambda vin: vin == petrol,
        # Contradicts the line above on purpose: an entity both rules name is
        # still removed only once.
        plug_in_hybrid=lambda vin: vin == petrol,
    )

    assert [(fix.entity_id, fix.remove) for fix in fixes] == [
        ("sensor.i5_header", False),
        ("sensor.m240_real_range", True),
        ("sensor.m240_soc_estimate", True),
        ("sensor.i5_testing", True),
    ]
    assert "disabled by default" in fixes[0].reason
    assert "fuel system" in fixes[1].reason
    assert fixes[3].reason == "this release no longer makes it"


def test_a_clean_registry_needs_no_fixes() -> None:
    rows = [row("sensor.i5_header", f"{VIN}_{HEADER}")]

    assert (
        setup.registry_fixes(
            rows, metadata, combustion_only=lambda _vin: False, plug_in_hybrid=lambda _vin: False
        )
        == []
    )
