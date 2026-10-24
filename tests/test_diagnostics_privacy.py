"""The diagnostics download names no entity the user picked.

Users attach this file to public issues because we ask them to, so anything in
it is published. Entity ids are free text: a phone tracker carries its owner's
name, a work zone the employer, an energy sensor a meter serial (it happened).
"""

from __future__ import annotations

import ast
import pathlib

from .conftest import load_module

privacy = load_module("diagnostics_privacy")
const = load_module("const")

_PKG = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "bavariandata"
# Every module a flow's forms are built in.
_FLOWS = ("config_flow.py", "options_flow.py", "options_cars.py")


def _entity_picker_keys() -> set[str]:
    """Every option key the flows ask for with an entity selector."""

    keys: set[str] = set()
    trees = [ast.parse((_PKG / name).read_text(encoding="utf-8")) for name in _FLOWS]
    for node in (node for tree in trees for node in ast.walk(tree)):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=True):
            if not (isinstance(value, ast.Call) and "EntitySelector" in ast.unparse(value.func)):
                continue
            # vol.Optional(OPTION_X, ...) / vol.Required(OPTION_X, ...)
            if isinstance(key, ast.Call) and key.args and isinstance(key.args[0], ast.Name):
                keys.add(getattr(const, key.args[0].id))
    return keys


def test_every_entity_picker_is_redacted():
    keys = _entity_picker_keys()
    # The flow really was read: price, meter, three power sensors, zone, phone.
    assert len(keys) >= 8
    assert keys <= privacy.ENTITY_ID_OPTIONS, keys - privacy.ENTITY_ID_OPTIONS


def test_entity_ids_reduced_to_domain_also_per_car():
    vin = "WBA00000000000000"
    safe = privacy.safe_options(
        {
            const.OPTION_PV_POWER_ENTITY: "sensor.evcc_pv_power",
            const.OPTION_TRIP_PHONE_TRACKER: "device_tracker.bens_iphone",
            const.OPTION_VEHICLES: {vin: {const.OPTION_TRIP_WORK_ZONE: "zone.acme_gmbh"}},
            "debug_log": True,
        }
    )
    assert safe[const.OPTION_PV_POWER_ENTITY] == "sensor.**REDACTED**"
    assert safe[const.OPTION_TRIP_PHONE_TRACKER] == "device_tracker.**REDACTED**"
    assert safe[const.OPTION_VEHICLES] == {
        privacy.fingerprint(vin): {const.OPTION_TRIP_WORK_ZONE: "zone.**REDACTED**"}
    }
    assert safe["debug_log"] is True
    assert vin not in repr(safe)
