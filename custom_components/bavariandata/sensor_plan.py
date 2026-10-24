"""Which sensors each car gets, apart from Home Assistant.

The sensor platform's setup used to decide this inline, in closures nothing
could test without Home Assistant: a petrol car must not get the battery
sensors, the cost sensors wait for a tariff, a sensor restored from the
registry comes back as itself rather than as a generic descriptor sensor on its
unique id, tyre sensors follow what BMW's diagnosis reports. ``SensorPlan``
holds those rules and remembers what it made; ``sensor.py`` builds the entity
each ``Want`` names and hands it to Home Assistant (``tests/test_sensor_plan.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Optional, Protocol

from .car_signals import DESC_ODOMETER
from .coverage import HIGH_VOLTAGE_SIGNALS, is_ev_only_descriptor
from .descriptors import OPT_IN_LOCATION_DESCRIPTORS, TRACKER_ONLY_LOCATION_DESCRIPTORS
from .tyre import has_tyre_data

# A streamed descriptor, and the opt-in latitude/longitude sensors (created
# disabled); ``Want.key`` is the descriptor.
DESCRIPTOR = "descriptor"
LOCATION = "location"
# One tyre's diagnosis; ``Want.key`` is the wheel position.
TYRE_WHEEL = "tyre"
TYRE_STATUS = "tyre_status"

# The derived sensors, by the unique-id suffix each one is made under.
SOC_TRACKING = (
    "soc_estimate",
    "soc_rate",
    "charge_eta",
    "charged_energy_total",
    "charged_energy_session",
)
CHARGING_ENERGY_MONTH = "charging_energy_month"
CHARGING_COST = ("charging_cost_month", "charging_cost_session")
CHARGING_COST_PER_DISTANCE = "charging_cost_per_100km"
CHARGING_SUMMARY = (CHARGING_ENERGY_MONTH, *CHARGING_COST, CHARGING_COST_PER_DISTANCE)
BATTERY_HEALTH = "battery_health"
DRIVING_DISTANCE = "driving_distance_month"
REAL_RANGE = "real_range"
VEHICLE_REPORT = "last_vehicle_report"
MILEAGE_ALLOWANCE = "mileage_allowance"

# A car that reports any of these can produce trips worth summarising; a device
# that streams none (never driven, no odometer) gets no trip sensor rather than
# one stuck at 0 km. Both odometer spellings count: the i5 streams
# ``travelledDistance`` and never ``mileage``, and gating on the latter alone
# left the real car with no monthly-distance sensor at all.
DRIVE_SIGNALS = (*DESC_ODOMETER, "vehicle.isMoving")

# Any of these streaming marks the car as having an HV battery worth tracking; a
# pure-ICE car streams none of them. Shared with the coverage self-test so "is
# this an EV" has one answer.
EV_SIGNALS = tuple(sorted(HIGH_VOLTAGE_SIGNALS))

# The account's diagnostics, by sensor type.
DIAGNOSTIC_TYPES = ("connection_status", "last_message", "last_telematic_api")
QUOTA_SUFFIX = "api_quota_remaining"


@dataclass(frozen=True)
class Want:
    """One sensor to make: its kind, its car and, for some kinds, a key."""

    kind: str
    vin: str
    key: str = ""


class SensorHost(Protocol):
    """What the plan reads off the coordinator."""

    history: Any
    pricing: Any
    tyre_diagnosis: Mapping[str, Any]

    def get_state(self, vin: str, descriptor: str) -> Any: ...

    def is_plug_in_hybrid(self, vin: str) -> bool: ...

    def is_combustion_only(self, vin: str) -> bool: ...


# Makes the entities for a batch, in order, and adds them to Home Assistant;
# the flag is ``update_before_add``.
AddEntities = Callable[[list[Want], bool], list[Any]]


def diagnostic_unique_id(entry_id: str, suffix: str) -> str:
    return f"{entry_id}_diagnostics_{suffix}"


def legacy_unique_ids(entry_id: str) -> dict[str, str]:
    """Diagnostics unique ids an older release used, to the ones they became."""

    return {
        f"{entry_id}_connection_status": diagnostic_unique_id(entry_id, "connection_status"),
        f"{entry_id}_last_message": diagnostic_unique_id(entry_id, "last_message"),
    }


def retired_unique_ids(entry_id: str) -> list[str]:
    """Account sensors no release makes any more, to remove from the registry."""

    return [diagnostic_unique_id(entry_id, "soc_rate")]


def add_diagnostic(disabled: bool, live: bool) -> bool:
    """Whether to add an account diagnostic the registry may already hold.

    Not when the user disabled it, and not when one is already live (a state
    that is not merely restored from the registry).
    """

    return not (disabled or live)


class SensorPlan:
    """The sensors one account has made, and which ones each car still needs."""

    def __init__(self, host: SensorHost, entry_id: str, add: AddEntities) -> None:
        self._host = host
        self._entry_id = entry_id
        self._add = add
        self._made: dict[Want, Any] = {}
        # The charging summary is made once per car, as a set: a tariff added
        # later does not bring the cost sensors until the next setup.
        self._charging: set[str] = set()

    def _make(self, wants: list[Want], update: bool) -> None:
        wants = [want for want in wants if want not in self._made]
        if not wants:
            return
        for want, entity in zip(wants, self._add(wants, update), strict=True):
            self._made[want] = entity

    def has(self, want: Want) -> bool:
        return want in self._made

    def _streams(self, vin: str, descriptors: Iterable[str]) -> bool:
        return any(self._host.get_state(vin, descriptor) is not None for descriptor in descriptors)

    def _has_battery(self, vin: str) -> bool:
        return self._streams(vin, EV_SIGNALS)

    def _has_odometer(self, vin: str) -> bool:
        return self._streams(vin, DESC_ODOMETER)

    def driving(self, vin: str, *, force: bool = False) -> None:
        """The monthly-distance sensor, once the car looks drivable.

        ``force`` re-creates one restored from the registry without re-checking
        for a live signal, matching how battery health avoids the generic path
        minting a bogus descriptor sensor on its id.
        """

        if self._host.history is None:
            return
        if force or self._streams(vin, DRIVE_SIGNALS):
            self._make([Want(DRIVING_DISTANCE, vin)], True)

    def battery_health(self, vin: str, *, force: bool = False) -> None:
        """The battery-health sensor, once the car looks like an EV.

        It self-explains via "Learning (n/10)" until it has data, so it is made
        as soon as any HV-battery signal appears -- but not for a pure-ICE car
        that will never charge, where it would sit at "Learning (0/10)" forever.
        """

        if self._host.history is None:
            return
        if force or self._has_battery(vin):
            self._make([Want(BATTERY_HEALTH, vin)], True)

    def real_range(self, vin: str, *, force: bool = False) -> None:
        """The real-range sensor, for an EV that reports its odometer.

        Both halves are required by the arithmetic, not by taste: the range is
        usable capacity over consumption, and the consumption comes from the
        distance between two charging sessions' odometer readings. A car with no
        odometer would hold an entity that can never produce a number.

        Never for a plug-in hybrid, forced or not: its odometer includes the
        kilometres the engine drove, so the range would read far too good
        (``summary.energy_balance``). One created before the fuel system first
        reported is removed at the next setup (``hybrid_entities_to_remove``).
        """

        if self._host.history is None or self._host.is_plug_in_hybrid(vin):
            return
        if force or (self._has_odometer(vin) and self._has_battery(vin)):
            self._make([Want(REAL_RANGE, vin)], True)

    def charging_summary(self, vin: str, *, force: bool = False) -> None:
        """The ledger sensors, but only once they can say something true.

        The energy total works for any car that charges -- so not for a petrol
        or diesel car, which gets none of them. The cost sensors need a
        configured tariff, so until one exists they are not created at all
        rather than sitting at "unknown" and inviting the question of what's
        broken.
        """

        if vin in self._charging or self._host.history is None:
            return
        if not (force or self._has_battery(vin)):
            return
        kinds = [CHARGING_ENERGY_MONTH]
        if self._host.pricing.enabled:
            kinds.extend(CHARGING_COST)
            # Distance-based cost is meaningless without an odometer, and the
            # Vehicle status cluster is optional in the portal. Not on a plug-in
            # hybrid either, whose odometer also counts the kilometres driven on
            # fuel (see ``summary.summarise``).
            if self._has_odometer(vin) and not self._host.is_plug_in_hybrid(vin):
                kinds.append(CHARGING_COST_PER_DISTANCE)
        self._charging.add(vin)
        self._make([Want(kind, vin) for kind in kinds], True)

    def soc_tracking(self, vin: str, *, force: bool = False) -> None:
        """The state-of-charge estimate and charged-energy sensors.

        Only for a car with a high-voltage battery: a petrol car used to get all
        five, stuck at "unknown" for good. ``force`` re-creates ones restored
        from the registry before the battery has reported today; entities a
        combustion car was given by an older release are removed at setup
        (``registry_repair.ev_entities_to_remove``) before this runs.
        """

        if force or self._has_battery(vin):
            self._make([Want(kind, vin) for kind in SOC_TRACKING], True)

    def tyres(self, vin: str, *, positions: Any = None, status: bool = False) -> None:
        """The tyre-diagnosis sensors for whichever wheels BMW reported.

        Only wheels present in the payload get an entity: a car with no tyre
        service record on file would otherwise gain four sensors permanently
        reading "unknown". ``positions``/``status`` re-create ones restored from
        the registry, before the first fetch of the day has landed.
        """

        if positions is None:
            diagnosis = self._host.tyre_diagnosis.get(vin) or {}
            positions = diagnosis.get("wheels") or {}
        wanted = set(positions)
        wants = [Want(TYRE_STATUS, vin)] if wanted or status else []
        wants.extend(Want(TYRE_WHEEL, vin, position) for position in sorted(wanted))
        self._make(wants, True)

    def tyre_data_absent(self, vin: str) -> bool:
        """A fetch landed and said BMW keeps no tyre data for this car (#174)."""

        diagnosis = self._host.tyre_diagnosis.get(vin)
        return diagnosis is not None and not has_tyre_data(diagnosis)

    def tyre_diagnosis(self, vin: str) -> list[Any]:
        """A diagnosis landed: the tyre entities whose registry entries must go.

        A later fetch can report a wheel the first one omitted, so this runs on
        every diagnosis. Pruned only on the "nothing at all" answer: a fetch
        that omits one wheel keeps that wheel's sensor and its history.
        """

        if not self.tyre_data_absent(vin):
            self.tyres(vin)
            return []
        gone = [want for want in self._made if want.vin == vin and want.kind in _TYRE_KINDS]
        return [self._made.pop(want) for want in gone]

    def locations(self, vin: str) -> None:
        """The opt-in latitude/longitude sensors, disabled, once per car.

        Per car rather than per descriptor: a descriptor only reaches
        ``descriptor`` when it is *new* to the coordinator, and the device
        tracker restores the last position into it at startup, so a live fix is
        never new and the sensors would never be created. Creating them from any
        message puts them in the registry (disabled) right away, ready to enable.
        """

        self._make(
            [Want(LOCATION, vin, descriptor) for descriptor in sorted(OPT_IN_LOCATION_DESCRIPTORS)],
            False,
        )

    def report(self, vin: str, *, force: bool = False) -> None:
        """The last-vehicle-report sensor (disabled), once the car has an odometer."""

        if force or self._has_odometer(vin):
            self._make([Want(VEHICLE_REPORT, vin)], False)

    def mileage(self, vin: str, *, force: bool = False) -> None:
        """The mileage allowance balance (disabled), for a car with an odometer."""

        if force or self._has_odometer(vin):
            self._make([Want(MILEAGE_ALLOWANCE, vin)], False)

    def car(self, vin: str) -> None:
        """Every per-car sensor the car's data supports so far."""

        self.soc_tracking(vin)
        self.locations(vin)
        self.report(vin)
        self.mileage(vin)
        self.charging_summary(vin)
        self.battery_health(vin)
        self.driving(vin)
        self.real_range(vin)

    def descriptor(self, vin: str, descriptor: str, *, assume_sensor: bool = False) -> None:
        """A descriptor reached the coordinator: its sensor, and the car's others."""

        self.car(vin)
        if self.has(Want(LOCATION, vin, descriptor)):
            return
        if is_ev_only_descriptor(descriptor) and self._host.is_combustion_only(vin):
            # BMW streams some battery fields to petrol cars too (a trip-end
            # battery level, the EV charge target); they never mean anything.
            return
        # The position belongs to the device tracker. Heading never becomes a
        # sensor; latitude and longitude are made by ``locations``.
        if descriptor in TRACKER_ONLY_LOCATION_DESCRIPTORS | OPT_IN_LOCATION_DESCRIPTORS:
            return
        state = self._host.get_state(vin, descriptor)
        if state:
            if isinstance(state.value, bool):
                return
        elif not assume_sensor:
            return
        self._make([Want(DESCRIPTOR, vin, descriptor)], False)

    def restore(self, unique_id: Optional[str]) -> bool:
        """Re-create a sensor the registry holds as what it was.

        Each derived sensor is routed to its own maker, or the generic path
        would mint a descriptor sensor on its unique id. ``False`` means the
        registry entry is stale and must be removed.
        """

        if not unique_id or "_" not in unique_id:
            return True
        if unique_id.startswith(f"{self._entry_id}_diagnostics_"):
            return True
        vin, descriptor = unique_id.split("_", 1)
        if descriptor in SOC_TRACKING:
            self.soc_tracking(vin, force=True)
        elif descriptor == BATTERY_HEALTH:
            self.battery_health(vin, force=True)
        elif descriptor == DRIVING_DISTANCE:
            self.driving(vin, force=True)
        elif descriptor == REAL_RANGE:
            self.real_range(vin, force=True)
        elif descriptor == VEHICLE_REPORT:
            self.report(vin, force=True)
        elif descriptor == MILEAGE_ALLOWANCE:
            self.mileage(vin, force=True)
        elif descriptor.startswith("tyre_"):
            if self.tyre_data_absent(vin):
                # Created before #174 from a diagnosis of BMW's defaults alone.
                return False
            # Re-create what the car had before today's fetch lands, so the
            # entity keeps its id and history.
            if descriptor == TYRE_STATUS:
                self.tyres(vin, positions=set(), status=True)
            else:
                self.tyres(vin, positions={descriptor[len("tyre_") :]})
        elif descriptor in CHARGING_SUMMARY:
            self.charging_summary(vin, force=True)
        else:
            self.descriptor(vin, descriptor, assume_sensor=True)
        return True

    def known(
        self,
        descriptors: Iterable[tuple[str, str]],
        vins: Iterable[str],
        tyre_vins: Iterable[str],
    ) -> None:
        """Everything the coordinator already holds when the platform starts."""

        for vin, descriptor in descriptors:
            self.descriptor(vin, descriptor)
        for vin in vins:
            self.car(vin)
        for vin in tyre_vins:
            # The diagnosis restored from the tyre store covers the registry
            # loop's case, but also the one it can't: a vehicle whose tyre
            # entities were removed from the registry still gets them back now
            # rather than waiting for the next daily fetch.
            self.tyres(vin)


_TYRE_KINDS = frozenset({TYRE_WHEEL, TYRE_STATUS})
