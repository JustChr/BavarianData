"""The card's automatic picks, run against what real cars stream.

``tests/cars/*.json`` are reduced diagnostics dumps from users' issues (see
``tools/anonymize_diagnostics.py``): per car, the model name and each streamed
descriptor with its unit. From one the test rebuilds the entities Home Assistant
would hold -- name and entity id from the shipped translations, device class from
the shipped metadata, boolean descriptors as binary sensors -- and runs the
shipped card's ``_overviewEntities`` on them under Node.

Two properties are pinned across every car:

* the pick is the same in all ten languages, because the descriptor is the
  language-independent part and the localized names must not decide anything;
* the pick is the same whatever the vehicle is called. Issue #51: a car named
  "iX3 M Sport" lost its charging status because "Sport" contains "port".

Only the electric and plug-in cars assert charging, plug, target and time to
full: on a petrol car the overview never renders them, so what the keyword
fallback lands on there is not behaviour. A device class is approximated from the
metadata (the entity derives its own from the unit as well), so this checks the
picking, not the entity classes.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from tests.conftest import load_module
from tests.test_card_soc_pick import NODE, _picks

pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_INTEGRATION = _ROOT / "custom_components" / "bavariandata"
_CARS = sorted((_ROOT / "tests" / "cars").glob("*.json"))
_LANGUAGES = ("en", "de", "fr", "it", "es", "nl", "pl", "pt", "cs", "sv")

# Names chosen to contain the card's avoid-list words ("port", "trip", "stream",
# "lock", "cable", "history", "target", "testing") and its keywords.
_HOSTILE_NAMES = ("M Sport", "Trip Stream Lock", "Port Cable History", "Target Testing", "Range")

_P = "vehicle.drivetrain.electricEngine."
_ELECTRIC = {
    "soc": "vehicle.drivetrain.batteryManagement.header",
    "range": _P + "kombiRemainingElectricRange",
    "charging": _P + "charging.status",
    "plug": "vehicle.body.chargingPort.status",
    "odometer": "vehicle.vehicle.travelledDistance",
}
_TARGET = "vehicle.powertrain.electric.battery.stateOfCharge.target"
_TIME_TO_FULL = _P + "charging.timeToFullyCharged"
_PETROL = {
    "range": "vehicle.drivetrain.lastRemainingRange",
    "odometer": "vehicle.vehicle.travelledDistance",
}

# car -> role -> the descriptor the pick must land on (``None``: nothing).
EXPECTED: dict[str, dict[str, str | None]] = {
    "i4-m60": {**_ELECTRIC, "target": _TARGET, "timeToFull": _TIME_TO_FULL},
    "i3s": {**_ELECTRIC, "timeToFull": _TIME_TO_FULL},
    "x3-30e-phev": {**_ELECTRIC, "timeToFull": _TIME_TO_FULL},
    "countryman-e": {k: v for k, v in _ELECTRIC.items() if k != "soc"} | {"target": _TARGET},
    "ix3-m-sport": {**_ELECTRIC, "target": _TARGET, "timeToFull": _TIME_TO_FULL},
    "ix-xdrive40": {**_ELECTRIC, "timeToFull": _TIME_TO_FULL},
    "m240i-xdrive": _PETROL,
    "m2-competition": _PETROL,
}

_META = load_module("descriptor_metadata").DESCRIPTOR_META
_CATALOGUE = {
    e["descriptor"]: e
    for e in json.loads((_INTEGRATION / "catalogue.json").read_text(encoding="utf-8"))[
        "descriptors"
    ]
}
_TRANSLATIONS = {
    lang: json.loads((_INTEGRATION / "translations" / f"{lang}.json").read_text(encoding="utf-8"))[
        "entity"
    ]
    for lang in _LANGUAGES
}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _states(car: dict, lang: str, vehicle: str) -> tuple[dict, dict[str, str]]:
    """The entities Home Assistant would hold for ``car``, named in ``lang``."""

    states: dict[str, dict] = {}
    by_descriptor: dict[str, str] = {}
    for row in car["descriptors"]:
        descriptor = row["descriptor"]
        domain = (
            "binary_sensor"
            if _CATALOGUE.get(descriptor, {}).get("data_type") == "boolean"
            else "sensor"
        )
        entry = _TRANSLATIONS[lang][domain].get(_slug(descriptor))
        if entry is None:
            continue
        entity_id = f"{domain}.{_slug(vehicle)}_{_slug(entry['name'])}"
        attributes = {"descriptor": descriptor, "friendly_name": f"{vehicle} {entry['name']}"}
        if row["unit"]:
            attributes["unit_of_measurement"] = row["unit"]
        if _META.get(descriptor, {}).get("device_class"):
            attributes["device_class"] = _META[descriptor]["device_class"]
        states[entity_id] = {"state": "1", "entity_id": entity_id, "attributes": attributes}
        by_descriptor[entity_id] = descriptor
    return states, by_descriptor


def _picked_descriptors(car: dict, lang: str, vehicle: str) -> dict[str, str]:
    states, by_descriptor = _states(car, lang, vehicle)
    return {role: by_descriptor[entity_id] for role, entity_id in _picks(states, vehicle).items()}


def _car(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_every_car_fixture_has_an_expectation():
    assert {p.stem for p in _CARS} == set(EXPECTED)


@pytest.mark.parametrize("path", _CARS, ids=lambda p: p.stem)
def test_picks_land_on_the_right_descriptor(path):
    car = _car(path)
    picked = _picked_descriptors(car, "en", car["name"])
    for role, descriptor in EXPECTED[path.stem].items():
        assert picked.get(role) == descriptor, role


@pytest.mark.parametrize("path", _CARS, ids=lambda p: p.stem)
def test_picks_do_not_depend_on_the_language(path):
    car = _car(path)
    expected = _picked_descriptors(car, "en", car["name"])
    for lang in _LANGUAGES[1:]:
        assert _picked_descriptors(car, lang, car["name"]) == expected, lang


@pytest.mark.parametrize("path", _CARS, ids=lambda p: p.stem)
def test_picks_do_not_depend_on_the_vehicles_name(path):
    car = _car(path)
    expected = _picked_descriptors(car, "en", car["name"])
    for name in _HOSTILE_NAMES:
        assert _picked_descriptors(car, "en", name) == expected, name
