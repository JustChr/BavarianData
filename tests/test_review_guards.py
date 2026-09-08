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
