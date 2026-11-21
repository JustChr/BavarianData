"""Where a car is, in Home Assistant's terms: its position, the zone, Home.

The coordinator keeps the readings; this turns them into places. A charge is
costed and a trip filed by the place it happened at, an event fires on leaving
a zone, and the route weather needs a stored zone's position back -- all of
which used to be private coordinator methods the engines, the services and the
device tracker reached into.

Two lookups, on purpose. :meth:`Places.ha_zone` answers as Home Assistant does
(the *smallest* zone containing the point, so a carport zone wins over Home)
and is what zone events use, so an automation on that carport still fires.
:meth:`Places.zone_name` is what records are filed under: anywhere at the house
is Home, whatever smaller zone lies inside it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .history.sessions import at_the_house

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .car_state import DescriptorState

# Where a car's position is read from, best first: the live navigation fix,
# then the end of the last trip segment (cars that report only once parked).
_POSITION_DESCRIPTORS = (
    "vehicle.cabin.infotainment.navigation.currentLocation.{kind}",
    "vehicle.trip.segment.end.vehicleLocation.gpsPosition.{kind}",
)


class Places:
    """One account's view of where its cars are."""

    def __init__(
        self,
        hass: HomeAssistant,
        get_state: Callable[[str, str], DescriptorState | None],
        *,
        remember_locations: Callable[[], bool],
        work_zone_entity: Callable[[str], str | None],
    ) -> None:
        self._hass = hass
        self._get_state = get_state
        self._remember_locations = remember_locations
        self._work_zone_entity = work_zone_entity

    def coordinate(self, vin: str, kind: str) -> float | None:
        """The car's ``latitude`` or ``longitude`` (``kind``), or ``None``."""

        for template in _POSITION_DESCRIPTORS:
            state = self._get_state(vin, template.format(kind=kind))
            if state is None or state.value is None:
                continue
            try:
                return float(state.value)
            except TypeError, ValueError:
                continue
        return None

    def charging_location(self, vin: str) -> dict[str, Any] | None:
        """Where the car is plugged in, as an HA zone name when we can tell.

        Knowing a session happened "at home" is what costing needs, so the zone
        is all that is kept -- unless the user turned on *Remember charging
        locations*, which adds the point itself for the charging places map. A
        list of exact positions is far more sensitive than a zone name, so that
        stays the user's choice.
        """

        latitude = self.coordinate(vin, "latitude")
        longitude = self.coordinate(vin, "longitude")
        if latitude is None or longitude is None:
            return None
        zone = self.zone_name(latitude, longitude)
        location: dict[str, Any] = {"zone": zone} if zone else {"zone": None}
        if self._remember_locations():
            location["lat"] = round(latitude, 5)
            location["lon"] = round(longitude, 5)
        return location

    def zone_name(self, latitude: float, longitude: float) -> str | None:
        """The place a charge or trip is filed under: Home wherever it is the house.

        Only places, not zone events: those keep the zone Home Assistant names
        (:meth:`ha_zone`), so an automation on a carport zone still fires.
        """

        found = self.ha_zone(latitude, longitude)
        if found is None:
            return None
        if found[1] != "zone.home":
            home = self._hass.states.get("zone.home")
            if home is not None and at_the_house(latitude, longitude, home.attributes):
                return self.home_zone_name()
        return found[0]

    def ha_zone(self, latitude: float, longitude: float) -> tuple[str, str] | None:
        """The zone a point lies in, as ``(name, entity_id)``, or ``None``."""

        from homeassistant.components import zone as zone_component

        try:
            found = zone_component.async_active_zone(self._hass, latitude, longitude)
        except Exception:  # noqa: BLE001 - zone lookup is best-effort
            return None
        if found is None:
            return None
        return (found.name or found.entity_id, found.entity_id)

    def current_ha_zone(self, vin: str) -> tuple[str, str] | None:
        """The zone the car is in now, as :meth:`ha_zone` answers it."""

        latitude = self.coordinate(vin, "latitude")
        longitude = self.coordinate(vin, "longitude")
        if latitude is None or longitude is None:
            return None
        return self.ha_zone(latitude, longitude)

    def zone_location(self, name: str) -> tuple[float, float] | None:
        """Where the zone called ``name`` is, for a stored trip's weather."""

        for state in self._hass.states.async_all("zone"):
            if state.name == name:
                lat, lon = state.attributes.get("latitude"), state.attributes.get("longitude")
                if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                    return (float(lat), float(lon))
        return None

    def home_location(self) -> tuple[float, float] | None:
        """The house's position, from Home Assistant's own configuration."""

        lat, lon = self._hass.config.latitude, self._hass.config.longitude
        if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
            return (float(lat), float(lon))
        return None

    def home_zone_name(self) -> str | None:
        """What Home is called (a renamed Home keeps its records together)."""

        state = self._hass.states.get("zone.home")
        return state.name if state is not None else "Home"

    def work_zone_name(self, vin: str) -> str | None:
        """The name of the zone this car's trips count as work, if one is set."""

        entity_id = self._work_zone_entity(vin)
        if not entity_id:
            return None
        state = self._hass.states.get(entity_id)
        return state.name if state is not None else None
