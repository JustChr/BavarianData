"""Guards on the things HACS review actually rejects integrations for.

A survey of ~170 reviewed submissions to `hacs/default` shows the code findings
clustering into a handful of repeating classes: TLS verification switched off,
an unauthenticated HTTP surface, secrets reaching logs or diagnostics, an
undeclared dependency on another integration, and a bloated payload shipped to
every install. None of that is exotic -- it is the same short list every time,
and this repo has been audited against it by hand more than once.

So the audit lives here instead. Each test pins one of those classes, with the
reason it matters, so a regression fails in CI rather than in a review comment
months later. These are deliberately *shape* checks over the shipped source --
Home Assistant-free, like the rest of the suite.

The bundled card has its own, larger set of guards in
``test_card_escaping.py``; privacy of shipped example data is in
``test_services_and_privacy.py``.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
import subprocess

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_PKG = _ROOT / "custom_components" / "bavariandata"

MANIFEST = json.loads((_PKG / "manifest.json").read_text(encoding="utf-8"))
HACS = json.loads((_ROOT / "hacs.json").read_text(encoding="utf-8"))


def _python_files() -> list[pathlib.Path]:
    return [
        p
        for p in _PKG.rglob("*.py")
        if "__pycache__" not in p.parts
    ]


PY_FILES = _python_files()


def _rel(path: pathlib.Path) -> str:
    return str(path.relative_to(_ROOT))


# --------------------------------------------------------------------------
# Transport security
# --------------------------------------------------------------------------

# Every way seen in the wild of turning off certificate validation. All of them
# were blocking findings, and all of them were on cloud endpoints -- which is
# exactly what this integration talks to.
TLS_BYPASSES = (
    "CERT_NONE",
    "check_hostname = False",
    "check_hostname=False",
    "tls_insecure_set(True)",
    "tls_insecure=True",
    "verify=False",
    "verify_ssl=False",
    "_create_unverified_context",
)


@pytest.mark.parametrize("path", PY_FILES, ids=_rel)
def test_tls_verification_is_never_disabled(path: pathlib.Path) -> None:
    source = path.read_text(encoding="utf-8")
    for bypass in TLS_BYPASSES:
        assert bypass not in source, (
            f"{_rel(path)} disables TLS verification via {bypass!r}. BMW's broker "
            "and REST API are both cloud endpoints; validation stays on."
        )


def test_tls_floor_is_never_a_ceiling() -> None:
    """A capped handshake breaks the stream: BMW's broker only speaks TLS 1.3."""

    tree = ast.parse((_PKG / "stream.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                assert not (
                    isinstance(target, ast.Attribute) and target.attr == "maximum_version"
                ), (
                    f"stream.py:{node.lineno} sets ssl maximum_version; BMW's broker "
                    "only negotiates TLS 1.3 and a ceiling below it fails with "
                    '"tlsv1 alert protocol version".'
                )


# --------------------------------------------------------------------------
# HTTP surface
# --------------------------------------------------------------------------

# Unauthenticated views are the second-most-common blocking finding. Exactly one
# is justified here -- the onboarding helper page, which the user's browser opens
# from BMW's portal with no Home Assistant credentials, gated instead on a
# one-time capability token and serving read-only HTML. Its class docstring
# spells out the reasoning. A *second* one must not appear without that argument
# being made again, so this pins the count rather than banning the pattern.
KNOWN_UNAUTHENTICATED_VIEWS = {"_OnboardingHelperView"}


def test_unauthenticated_views_stay_known_and_justified() -> None:
    found: dict[str, ast.ClassDef] = {}
    for path in PY_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for statement in node.body:
                if (
                    isinstance(statement, ast.Assign)
                    and any(
                        isinstance(t, ast.Name) and t.id == "requires_auth"
                        for t in statement.targets
                    )
                    and isinstance(statement.value, ast.Constant)
                    and statement.value.value is False
                ):
                    found[node.name] = node

    assert set(found) == KNOWN_UNAUTHENTICATED_VIEWS, (
        "the set of unauthenticated HTTP views changed: "
        f"{sorted(set(found) ^ KNOWN_UNAUTHENTICATED_VIEWS)}. An unauthenticated "
        "view needs a documented reason it cannot require auth, and must stay "
        "read-only and capability-gated."
    )
    for name, node in found.items():
        doc = ast.get_docstring(node) or ""
        assert len(doc.splitlines()) > 3, (
            f"{name} is unauthenticated but carries no explanation; the docstring "
            "is what a reviewer reads first."
        )


def test_no_routes_in_home_assistants_reserved_namespace() -> None:
    """Squatting ``/api/`` collides with core and bypasses expectations."""

    for path in PY_FILES:
        for match in re.finditer(r'^\s*url\s*=\s*["\'](/[^"\']*)', path.read_text(encoding="utf-8"), re.MULTILINE):
            assert not match.group(1).startswith("/api/"), (
                f"{_rel(path)} registers {match.group(1)!r} under Home Assistant's "
                "reserved /api/ namespace"
            )


# --------------------------------------------------------------------------
# Secrets
# --------------------------------------------------------------------------

# Debug logs are the first thing a maintainer asks a user to paste into a public
# issue, so a credential reaching one is treated as a leak. Diagnostics downloads
# are shared the same way.
SECRET_TERMS = ("access_token", "refresh_token", "id_token", "client_secret", "password")


def _logged_secret(arg: ast.expr) -> str | None:
    """The secret this log argument would render, if it renders one.

    Only the *value* matters. Deriving something harmless from a secret is fine
    and the code does it deliberately -- ``len(self._password or "")`` proves a
    token arrived without disclosing it -- so this looks at the shape of the
    whole argument rather than searching its text.
    """

    name: str | None = None
    if isinstance(arg, ast.Name):
        name = arg.id
    elif isinstance(arg, ast.Attribute):
        name = arg.attr
    elif isinstance(arg, ast.Subscript) and isinstance(arg.slice, ast.Constant):
        name = arg.slice.value if isinstance(arg.slice.value, str) else None
    elif (
        isinstance(arg, ast.Call)
        and isinstance(arg.func, ast.Attribute)
        and arg.func.attr == "get"
        and arg.args
        and isinstance(arg.args[0], ast.Constant)
        and isinstance(arg.args[0].value, str)
    ):
        name = arg.args[0].value

    if not name:
        return None
    lowered = name.lower()
    return next((term for term in SECRET_TERMS if term in lowered), None)


@pytest.mark.parametrize("path", PY_FILES, ids=_rel)
def test_no_credentials_are_logged(path: pathlib.Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr in {"debug", "info", "warning", "error", "exception"}
        ):
            continue
        for arg in node.args:
            secret = _logged_secret(arg)
            assert secret is None, (
                f"{_rel(path)}:{node.lineno} logs {ast.unparse(arg)!r}, which "
                f"renders {secret!r}. Debug logs get pasted into public issues -- "
                "log a length or a boolean instead."
            )


# Levels that reach ``home-assistant.log`` on a default install. ``debug`` is
# absent on purpose: verbose logging is opt-in precisely because it carries the
# VIN and GPS position, and triage needs the full value there.
DEFAULT_LOG_LEVELS = {"info", "warning", "error", "exception", "critical"}


@pytest.mark.parametrize("path", PY_FILES, ids=_rel)
def test_vin_is_masked_in_default_level_logs(path: pathlib.Path) -> None:
    """A VIN is personal data, and default-level logs get pasted into issues."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr in DEFAULT_LOG_LEVELS
            and isinstance(func.value, ast.Name)
            and "LOG" in func.value.id.upper()
        ):
            continue
        # The first argument is the format string; the rest are the values.
        for arg in node.args[1:]:
            bare_vin = (isinstance(arg, ast.Name) and arg.id == "vin") or (
                isinstance(arg, ast.Attribute) and arg.attr == "vin"
            )
            assert not bare_vin, (
                f"{_rel(path)}:{node.lineno} logs a bare VIN at .{func.attr}(); "
                "wrap it in mask_vin() or move the line to .debug()."
            )


# Keys that must never survive into a diagnostics download. ``topic`` is here
# because the MQTT topic embeds the account's GCID.
MUST_REDACT = {
    "vin",
    "gcid",
    "client_id",
    "id_token",
    "access_token",
    "refresh_token",
    "password",
    "latitude",
    "longitude",
    "topic",
}


def test_diagnostics_redacts_every_identifying_key() -> None:
    tree = ast.parse((_PKG / "diagnostics.py").read_text(encoding="utf-8"))
    redacted: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "TO_REDACT" for t in node.targets)
            and isinstance(node.value, ast.Set)
        ):
            redacted = {
                element.value
                for element in node.value.elts
                if isinstance(element, ast.Constant)
            }

    missing = MUST_REDACT - redacted
    assert not missing, f"diagnostics.py TO_REDACT is missing {sorted(missing)}"


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------

# Integrations whose code may be used without declaring a dependency: the entity
# platforms this integration implements, plus core pieces that are always set up.
# Anything else providing runtime behaviour we rely on -- ``webhook`` registers
# the HTTP route the onboarding activator posts to -- has to be declared, or it
# is missing on an install without ``default_config``.
IMPLICIT_COMPONENTS = {
    "binary_sensor",
    "device_tracker",
    "diagnostics",
    "image",
    "persistent_notification",
    "sensor",
    "zone",
}


def test_manifest_declares_every_integration_it_depends_on() -> None:
    used: set[str] = set()
    for path in PY_FILES:
        source = path.read_text(encoding="utf-8")
        used.update(re.findall(r"from homeassistant\.components\.(\w+)", source))
        for match in re.finditer(r"from homeassistant\.components import ([^\n]+)", source):
            for name in match.group(1).split(","):
                name = name.strip().split(" as ")[0].strip()
                if name.isidentifier():
                    used.add(name)

    declared = set(MANIFEST.get("dependencies", [])) | set(
        MANIFEST.get("after_dependencies", [])
    )
    undeclared = used - declared - IMPLICIT_COMPONENTS
    assert not undeclared, (
        f"manifest.json does not declare {sorted(undeclared)}, but the code uses "
        "them. Without a declaration they may not be set up on a minimal install."
    )


def test_manifest_carries_what_hacs_requires() -> None:
    for key in (
        "domain",
        "name",
        "version",
        "documentation",
        "issue_tracker",
        "codeowners",
    ):
        assert MANIFEST.get(key), f"manifest.json is missing {key!r}"

    assert MANIFEST["domain"] == _PKG.name
    assert re.fullmatch(r"\d+\.\d+\.\d+(?:-[\w.]+)?", MANIFEST["version"]), (
        f"version {MANIFEST['version']!r} is not semver; HACS sorts releases by it"
    )
    for key in ("documentation", "issue_tracker"):
        assert "JustChr/BavarianData" in MANIFEST[key], (
            f"manifest.json {key} must point at this repo, not someone else's"
        )


def test_hacs_json_matches_the_release_artifact() -> None:
    assert HACS.get("name"), "hacs.json needs a name"
    if HACS.get("zip_release"):
        assert HACS.get("filename") == "bavariandata.zip", (
            "hacs.json declares zip_release, so filename must match the asset the "
            "release workflow attaches"
        )


def test_brand_assets_ship_with_the_integration() -> None:
    """Self-served since the brands repo stopped accepting custom integrations."""

    brand = _PKG / "brand"
    for asset in ("icon.png", "icon@2x.png", "logo.png"):
        assert (brand / asset).is_file(), f"brand/{asset} is missing"


# --------------------------------------------------------------------------
# Payload
# --------------------------------------------------------------------------

# A 39 MB APK and a 6.5 MB www/ have both been rejected. Every byte here is
# downloaded by every install on every update; the ceilings are generous but
# force a deliberate decision.
MAX_WWW_KB = 512
MAX_TOTAL_KB = 4096


def test_shipped_payload_stays_small() -> None:
    def _kb(paths) -> int:
        return sum(p.stat().st_size for p in paths if p.is_file()) // 1024

    shipped = [p for p in _PKG.rglob("*") if "__pycache__" not in p.parts]
    www_kb = _kb(p for p in shipped if "www" in p.parts)
    total_kb = _kb(shipped)

    assert www_kb <= MAX_WWW_KB, f"www/ is {www_kb} KB (max {MAX_WWW_KB})"
    assert total_kb <= MAX_TOTAL_KB, (
        f"the integration ships {total_kb} KB (max {MAX_TOTAL_KB})"
    )


def test_no_bytecode_is_committed() -> None:
    """Local ``__pycache__`` is fine; a *committed* one ships to every install."""

    try:
        tracked = subprocess.run(
            ["git", "ls-files", "custom_components"],
            cwd=_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
    except (OSError, subprocess.CalledProcessError):  # pragma: no cover
        pytest.skip("git is not available")

    bytecode = [p for p in tracked if p.endswith(".pyc") or "__pycache__" in p]
    assert not bytecode, f"compiled bytecode is committed: {bytecode}"


@pytest.mark.parametrize("path", PY_FILES, ids=_rel)
def test_no_debug_output(path: pathlib.Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ):
            pytest.fail(f"{_rel(path)}:{node.lineno} uses print(); log instead")


# --------------------------------------------------------------------------
# Descriptors the live features cannot work without
# --------------------------------------------------------------------------
#
# ``enabled_default`` is generated from a substring match in
# ``tools/generate_metadata.py``, and it does far more than collapse an entity:
# ``descriptors.descriptors_for_sections`` streams only what it marks, so the
# flag decides what the portal snippet ticks, what the stream activator turns
# on, what onboarding selects -- and what the coverage self-test expects. A
# descriptor that loses the flag is therefore never ticked in Data Selection,
# never arrives, and is not reported missing either.
#
# That is issue #6. The pattern ``.header`` matched exactly one descriptor in
# BMW's 295-field catalogue: ``vehicle.drivetrain.batteryManagement.header``,
# which despite the name is the high-voltage state of charge. An iX charged four
# times with no SoC on the wire, so the energy ceiling saw a rise of zero and
# filed every 100 kW DC charge as 1.4 kWh.
#
# These are the descriptors the coordinator reads off the *stream* to drive a
# feature, as opposed to the ones it reads from a REST container. Each has to be
# streamable and in the set the integration actually asks BMW for.

STREAM_CRITICAL_DESCRIPTORS = {
    # Charging sessions: SoC arc, the energy ceiling, the card's gauge.
    "vehicle.drivetrain.batteryManagement.header": "state of charge",
    # Scales SoC into energy, and is the ceiling's capacity term.
    "vehicle.drivetrain.batteryManagement.maxEnergy": "pack capacity",
    # Integrated into delivered energy, and drawn as the power curve.
    "vehicle.powertrain.electric.battery.charging.power": "charging power",
    # The transition that opens and closes a session and fires the events.
    "vehicle.drivetrain.electricEngine.charging.status": "charging status",
    "vehicle.powertrain.electric.battery.stateOfCharge.target": "charge target",
    # AC fallback when charging.power is absent (V x A x phases).
    "vehicle.drivetrain.electricEngine.charging.acVoltage": "AC voltage",
    "vehicle.drivetrain.electricEngine.charging.acAmpere": "AC current",
    "vehicle.drivetrain.electricEngine.charging.phaseNumber": "AC phases",
    # Trips, and the zone a charge is attributed to.
    "vehicle.cabin.infotainment.navigation.currentLocation.latitude": "position",
    "vehicle.cabin.infotainment.navigation.currentLocation.longitude": "position",
    # Trip distance and the mileage stamped on a session.
    "vehicle.vehicle.travelledDistance": "odometer",
}


def _activation_set() -> set[str]:
    """What the portal snippet, the activator and onboarding actually ask for."""

    import sys

    sys.path.insert(0, str(_PKG))
    try:
        import descriptors as D  # type: ignore[import-not-found]

        return set(D.descriptors_for_sections(D.default_sections()))
    finally:
        sys.path.remove(str(_PKG))


@pytest.mark.parametrize(
    ("descriptor", "role"), sorted(STREAM_CRITICAL_DESCRIPTORS.items())
)
def test_stream_critical_descriptors_are_activated(descriptor: str, role: str) -> None:
    assert descriptor in _activation_set(), (
        f"{descriptor} ({role}) is not in the default activation set, so Data "
        "Selection never ticks it and it never reaches the stream. Check it "
        "hasn't been caught by a _DIAGNOSTIC_PATTERNS substring in "
        "tools/generate_metadata.py -- see issue #6."
    )


# --------------------------------------------------------------------------
# Restoring a value without re-converting it
# --------------------------------------------------------------------------

# Issue #7: Home Assistant saves the value it *displayed*. Feeding
# ``last_state.state`` back into ``_attr_native_value`` therefore re-applies the
# display conversion on every restart -- a tyre pressure held in kPa but shown in
# bar divides by 100 each time, and five restarts is the 2.5e-10 bar that was
# reported. It cannot self-correct, because the native unit never disagrees with
# itself; only a fresh reading from the stream resets it.
#
# ``CardataRestoreSensor.async_restored_native`` is the only safe way to read a
# saved value back: it prefers ``RestoreSensor``'s native data and falls back
# through ``restore_units``. Anything else touching ``async_get_last_state`` in
# the sensor platform has to say why it is exempt.

RAW_STATE_RESTORE_ALLOWED = {
    # The implementation of the safe path itself.
    "CardataRestoreSensor": "wraps the raw state in restore_units",
    # Timestamps and connection strings. No unit, no device class that converts,
    # so the saved state is the native value by construction.
    "CardataDiagnosticsSensor": "unitless timestamps and status strings",
}


def _class_of(tree: ast.Module, node: ast.AST) -> str | None:
    """Name of the class a node sits in, if any."""

    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        if any(child is node for child in ast.walk(cls)):
            return cls.name
    return None


def test_sensor_values_are_restored_in_native_units() -> None:
    path = _PKG / "sensor.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "async_get_last_state"):
            continue
        owner = _class_of(tree, node)
        assert owner in RAW_STATE_RESTORE_ALLOWED, (
            f"{_rel(path)}:{node.lineno}: {owner} reads the saved *displayed* "
            "state directly. Use CardataRestoreSensor.async_restored_native() so "
            "a display unit (a user override, or the one the US customary unit "
            "system picks by itself) is not re-applied on every restart -- see "
            "issue #7 and restore_units.py."
        )


def test_the_safe_restore_path_stores_native_data() -> None:
    """``RestoreSensor`` is what persists the native value and its unit.

    Downgrading the base back to ``RestoreEntity`` would silently return the
    platform to guessing from the display unit forever, rather than only across
    the one upgrade restart.
    """

    path = _PKG / "sensor.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    base = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "CardataRestoreSensor"
    )
    bases = {ast.unparse(b) for b in base.bases}
    assert "RestoreSensor" in bases, (
        f"CardataRestoreSensor inherits {sorted(bases)}; it must inherit "
        "RestoreSensor so native values and units are what get persisted."
    )


@pytest.mark.parametrize(
    "name",
    [
        "CardataSensor",
        "CardataSocEstimateSensor",
        "CardataTestingSocEstimateSensor",
        "CardataSocRateSensor",
        "CardataChargedEnergySensor",
        "CardataSessionEnergySensor",
    ],
)
def test_restoring_sensors_use_the_safe_base(name: str) -> None:
    path = _PKG / "sensor.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    cls = next(
        n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == name
    )
    bases = {ast.unparse(b) for b in cls.bases}
    assert "CardataRestoreSensor" in bases, (
        f"{name} restores a value across restarts, so it must derive from "
        f"CardataRestoreSensor; it inherits {sorted(bases)}."
    )


def _function_source(path, name: str) -> str:
    """The source of one top-level method, found by name anywhere in the file."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    node = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
    )
    return ast.unparse(node)


def test_an_in_progress_charge_is_snapshotted_on_the_way_out() -> None:
    """Unload and shutdown must persist a charge that is still running.

    This used to be a deliberate omission -- the active session was dropped on
    the assumption BMW's charging-history import would recover it, which it does
    not (that import is a manual, quota-costing service). Measured on a live
    instance, two restarts that landed mid-charge cost about 22 kWh in one week,
    and every total built on the ledger read low by exactly that much.
    """

    source = _function_source(_PKG / "coordinator.py", "async_flush_charging")
    assert "_session_builders" in source and "_snapshot_open_session" in source, (
        "async_flush_charging no longer snapshots the open sessions. A charge "
        "still running at unload or shutdown is then lost for good, taking its "
        "kWh, its cost and its statistics with it."
    )


def test_closing_a_session_drops_its_snapshot() -> None:
    """Otherwise the next start resurrects a charge that already got filed."""

    source = _function_source(_PKG / "coordinator.py", "_close_session_record")
    assert "_clear_open_session" in source, (
        "_close_session_record must clear the in-progress snapshot; leaving it "
        "behind would restore an already-recorded charge as a duplicate on the "
        "next restart."
    )


def test_home_assistant_shutdown_is_listened_for() -> None:
    """HA does not unload config entries on shutdown, so the flush needs a hook.

    Without this listener ``async_flush_charging`` runs only on a reload, which
    is the one restart flavour users never do.
    """

    path = _PKG / "__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    listens = any(
        isinstance(node, ast.Call)
        and ast.unparse(node.func).endswith("async_listen_once")
        and any(ast.unparse(arg) == "EVENT_HOMEASSISTANT_STOP" for arg in node.args)
        for node in ast.walk(tree)
    )
    assert listens, (
        "Nothing listens for EVENT_HOMEASSISTANT_STOP any more: an open charge "
        "or trip would only reach disk if it happened to be snapshotted in the "
        "last few minutes before the restart."
    )


def test_the_source_mix_is_sampled_with_the_cost() -> None:
    """One sample of the house's meters must serve both, or they disagree.

    Attributing at the end of a session instead would file a charge that began
    in sunshine and ended after dark under whichever came last -- the exact
    error the feature exists to avoid -- and a mix taken at a different instant
    from the price would let the cost and the solar share contradict each other.
    """

    source = _function_source(_PKG / "coordinator.py", "_record_energy_delta")
    assert "_session_mixes" in source and "_supply_shares" in source, (
        "_record_energy_delta no longer attributes the increment it is billing. "
        "The source mix has to be sampled on the same energy delta as the cost."
    )


def _calls_to(path, function: str, name: str) -> list:
    """Every call to ``name`` inside the given top-level function."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    scope = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == function
    )
    return [
        node
        for node in ast.walk(scope)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == name
    ]


def test_real_range_is_never_computed_from_the_grid_side_figure() -> None:
    """Range is capacity over what the car takes *out of the pack*.

    A grid-side consumption figure includes the charging losses, so dividing a
    battery capacity by it has the car driving on energy that never reached the
    pack -- a range that reads short, quietly, with nothing about it looking
    wrong. The two sides are computed separately on purpose; this pins which one
    reaches the arithmetic.
    """

    path = _PKG / "history" / "efficiency.py"
    calls = _calls_to(path, "efficiency_profile", "real_range")
    assert calls, "efficiency_profile no longer computes a range at all."
    for call in calls:
        rendered = ast.unparse(call)
        assert "grid" not in rendered, (
            "real_range() is being fed a grid-side figure: the charging losses "
            "would be driven as kilometres. It takes the battery-side "
            "consumption only."
        )
        assert "battery" in rendered, (
            "real_range() no longer takes the battery-side consumption figure."
        )


def test_the_measured_charging_loss_comes_from_one_window() -> None:
    """Both sides must describe the same kilometres, or the loss is fiction.

    ``consumption()`` walks widening windows until one can answer, so asking it
    for the grid side separately can land on a different period -- and the gap
    between a 30-day battery figure and a 365-day grid figure is a change in
    driving, reported as a charging loss.
    """

    path = _PKG / "history" / "efficiency.py"
    for call in _calls_to(path, "efficiency_profile", "consumption"):
        sides = [
            ast.unparse(kw.value) for kw in call.keywords if kw.arg == "side"
        ]
        assert "SIDE_GRID" not in sides, (
            "The grid-side figure is being fetched through the window-walking "
            "consumption() helper, which can settle on a different window than "
            "the battery-side one. It must be measured over the window the "
            "battery figure already chose."
        )
    grid_balances = [
        call
        for call in _calls_to(path, "efficiency_profile", "energy_balance")
        if any(
            kw.arg == "side" and ast.unparse(kw.value) == "SIDE_GRID"
            for kw in call.keywords
        )
    ]
    assert grid_balances, "The grid-side figure is no longer computed at all."
    for call in grid_balances:
        assert call.args and ast.unparse(call.args[0]) == "scope", (
            "The grid-side balance no longer reads the window the battery-side "
            "figure was measured over."
        )


def test_the_real_range_entity_watches_the_figures_it_is_measured_against() -> None:
    """A parked car must not be left showing what it knew at startup.

    The profile is recomputed on two signals -- a charge landing and the SoC
    estimate moving -- and neither fires for BMW's own remaining range or the
    pack capacity, which arrive as plain stream messages. Measured on the live
    instance after a restart: the entity held ``bmw_range_km: null`` while the
    same call through ``get_efficiency`` answered 379 km, so the card's whole
    comparison against BMW was missing and stayed missing, a parked car offering
    neither signal that would refresh it.
    """

    tree = ast.parse((_PKG / "sensor.py").read_text(encoding="utf-8"))
    cls = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "CardataRealRangeSensor"
    )
    source = ast.unparse(cls)
    assert "signal_update" in source, (
        "The real-range entity no longer subscribes to signal_update: BMW's "
        "range and the pack capacity would freeze at whatever they were when "
        "the entity was created."
    )
    assert "EFFICIENCY_LIVE_DESCRIPTORS" in source, (
        "The descriptor handler must filter on EFFICIENCY_LIVE_DESCRIPTORS -- "
        "recomputing the profile walks every stored session, so doing it for "
        "every message a car streams is a cost for nothing."
    )
