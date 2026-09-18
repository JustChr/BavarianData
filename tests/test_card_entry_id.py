"""Every service call the card makes has to name its config entry.

With two accounts set up -- a BMW and a MINI, for instance -- the services
cannot guess which entry a call means and refuse it outright ("multiple entries
configured; specify entry_id"), so the charging, trips, map and efficiency views
stay empty for as long as both accounts exist. The card therefore reads the
entry off the vehicle's device and passes it along.

The trap this pins is the *shape of the device registry*: `config_entry_id`
only exists from Home Assistant 2026.8, while the minimum supported here is
2026.3, where the field is `primary_config_entry`. Reading only the new name
yields `undefined` on an older install -- and since the services declare
`entry_id` as an optional *string*, sending it as null fails schema validation
and breaks the same views for everyone, one-account installs included. So the
rule is two-sided: resolve the entry from whichever field the registry offers,
and where none does, leave the key off entirely rather than send a null.

Like the other card tests this runs the shipped card under Node -- passing an
argument is behaviour, and a text match would pass on a rewrite that no longer
does it.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CARD = _ROOT / "custom_components" / "bavariandata" / "www" / "bavariandata-card.js"

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")

ENTRY = "01KYDMS7JW5ABEQAH7HXQAQ3F7"
VIN = "WBAEXAMPLE0000000"

# The device registry as Home Assistant has spelled it over time. Every shape
# has to resolve to the same entry, and the last one -- a device the registry
# does not know at all -- has to resolve to nothing without breaking the call.
REGISTRY_SHAPES = {
    "2026.8+": {
        "config_entry_id": ENTRY,
        "primary_config_entry": ENTRY,
        "config_entries": [ENTRY],
    },
    "2026.3": {"primary_config_entry": ENTRY, "config_entries": [ENTRY]},
    "legacy-list-only": {"config_entries": [ENTRY]},
    "unknown-device": None,
}

# Drives each fetch the way its render path does: resolve the entry off the
# device, hand it to the fetcher, and record what reached hass.callService.
_HARNESS = r"""
const fs = require("fs");
globalThis.HTMLElement = class {};
globalThis.customElements = { define() {}, get() {}, whenDefined() {} };
globalThis.window = globalThis;
console.info = () => {};  // the card's version banner would precede the JSON
const src = fs.readFileSync(process.argv[1], "utf8");
const Card = new Function(src + "\nreturn BavarianDataCard;")();

const input = JSON.parse(fs.readFileSync(0, "utf8"));
const calls = [];
const card = Object.create(Card.prototype);
card._config = { view: "charging" };
card._hass = {
  states: {},
  devices: input.device ? { dev: input.device } : {},
  callService: (domain, service, data) => {
    calls.push({ domain, service, data });
    return Promise.resolve({ response: {} });
  },
};

const entryId = card._deviceEntry("dev");
card._chg = {};
card._fetchCharging(input.vin, entryId, "2026-09");
card._trp = {};
card._fetchTrips(input.vin, entryId, "2026-09");
card._mapData = {};
card._fetchMapTrips(input.vin, entryId);
card._eff = {};
card._fetchEfficiency(input.vin, entryId);
// The export button reads the device itself rather than being handed an entry.
// Its response handling belongs to another test; here it only has to fire.
card.dispatchEvent = () => {};
card._notify = () => {};
card._resolveDeviceId = () => "dev";
card._deviceVin = () => input.vin;
card._month = () => "2026-09";
card._export("both", "csv");
// Re-classifying a trip reads the entry cached with the trip list.
card._trp = { vin: input.vin, entryId };
card._hass.callService(
  "bavariandata",
  "set_trip_class",
  card._withEntry(
    { vin: input.vin, trip_id: `${input.vin}-1`, classification: "private" },
    card._trp.entryId
  )
);

process.stdout.write(JSON.stringify({ entryId: entryId, calls }));
"""


def _run(device: dict | None) -> dict:
    proc = subprocess.run(
        [NODE, "-e", _HARNESS, "--", str(_CARD)],
        input=json.dumps({"device": device, "vin": VIN}),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# Every service the card calls; each one goes through the shared resolver, so
# each one has to be told which account it is for.
EXPECTED_SERVICES = {
    "get_charging_sessions",
    "get_trips",
    "get_driving_summary",
    "get_efficiency",
    "export_history",
    "set_trip_class",
}


@pytest.mark.parametrize("shape", ["2026.8+", "2026.3", "legacy-list-only"])
def test_entry_id_travels_with_every_call(shape: str) -> None:
    """A known device puts its entry id on every service call."""

    result = _run(REGISTRY_SHAPES[shape])
    assert result["entryId"] == ENTRY, f"{shape} registry did not resolve the entry"

    services = {call["service"] for call in result["calls"]}
    assert EXPECTED_SERVICES <= services, f"missing: {EXPECTED_SERVICES - services}"
    for call in result["calls"]:
        assert call["data"].get("entry_id") == ENTRY, call["service"]


def test_unknown_device_omits_entry_id_rather_than_sending_null() -> None:
    """No device, no key -- `entry_id: null` fails the services' schema.

    ``vol.Optional("entry_id"): str`` rejects a null outright, which would turn
    a card that merely cannot name its entry into a card that cannot fetch at
    all. Omitting it falls back to the single configured entry, which is right
    for the install that has only one.
    """

    result = _run(None)
    assert result["entryId"] is None

    services = {call["service"] for call in result["calls"]}
    assert EXPECTED_SERVICES <= services, f"missing: {EXPECTED_SERVICES - services}"
    for call in result["calls"]:
        assert "entry_id" not in call["data"], call["service"]


def test_no_call_site_builds_entry_id_by_hand() -> None:
    """The card only ever adds `entry_id` through `_withEntry`.

    A hand-written ``entry_id: entryId`` in a call's payload is how a null gets
    sent, so the helper is the only sanctioned way in -- its own definition
    aside.
    """

    source = _CARD.read_text(encoding="utf-8")
    body = source.replace("{ ...data, entry_id: entryId }", "")
    offenders = re.findall(r"entry_id:\s*\w+", body)
    assert not offenders, f"entry_id set outside _withEntry: {offenders}"
