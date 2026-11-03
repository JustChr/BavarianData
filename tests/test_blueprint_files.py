"""The shipped blueprints, installed into the user's blueprint folder and kept current.

``blueprint_files.sync`` runs here against a temporary configuration folder and
the real shipped files; ``plan`` is the rule on its own.
"""

from __future__ import annotations

from pathlib import Path

from .conftest import load_module

blueprint_files = load_module("blueprint_files")

ROOT = Path(__file__).parents[1]
# Where an import by URL comes from: the wiki links these, and every shipped
# blueprint's ``source_url`` points here.
PUBLISHED = ROOT / "blueprints" / "automation" / "bavariandata"


def _target(config: Path) -> Path:
    return config.joinpath(*blueprint_files.TARGET)


def test_the_shipped_blueprints_are_the_published_ones():
    """The integration ships the same files people import by URL, every one of them."""

    shipped = {path.name: path.read_bytes() for path in blueprint_files.SHIPPED.glob("*.yaml")}
    published = {path.name: path.read_bytes() for path in PUBLISHED.glob("*.yaml")}
    assert shipped, "no blueprints shipped"
    assert shipped.keys() == published.keys()
    stale = [name for name in shipped if shipped[name] != published[name]]
    assert not stale, (
        f"{stale} differ between custom_components/bavariandata/blueprints/ and "
        "blueprints/automation/bavariandata/: copy the one you edited over the other"
    )
    for name, data in shipped.items():
        assert f"blueprints/automation/bavariandata/{name}".encode() in data, f"{name}: source_url"


def test_a_new_install_gets_every_blueprint(tmp_path):
    result = blueprint_files.sync(str(tmp_path), {})

    shipped = sorted(path.name for path in blueprint_files.SHIPPED.glob("*.yaml"))
    assert list(result.write) == shipped
    assert result.replaces is False
    for name in shipped:
        assert (_target(tmp_path) / name).read_bytes() == (
            blueprint_files.SHIPPED / name
        ).read_bytes()
    assert set(result.record) == set(shipped)


def test_nothing_is_written_when_all_is_current(tmp_path):
    record = blueprint_files.sync(str(tmp_path), {}).record

    again = blueprint_files.sync(str(tmp_path), record)

    assert again.write == ()
    assert again.replaces is False
    assert again.record == record


def test_an_update_replaces_what_we_wrote():
    old, new = b"version: 1\n", b"version: 2\n"

    result = blueprint_files.plan(
        {"a.yaml": new}, {"a.yaml": old}, {"a.yaml": blueprint_files.digest(old)}
    )

    assert result.write == ("a.yaml",)
    assert result.replaces is True
    assert result.record == {"a.yaml": blueprint_files.digest(new)}


def test_a_file_changed_by_hand_is_left_alone_for_good():
    ours, theirs = b"version: 1\n", b"version: 1  # mine\n"
    record = {"a.yaml": blueprint_files.digest(ours)}

    result = blueprint_files.plan({"a.yaml": b"version: 2\n"}, {"a.yaml": theirs}, record)

    assert result.write == ()
    assert result.replaces is False
    # The record still names what we wrote, so the next update leaves it too.
    assert result.record == record
    later = blueprint_files.plan({"a.yaml": b"version: 3\n"}, {"a.yaml": theirs}, result.record)
    assert later.write == ()


def test_a_blueprint_imported_by_url_before_is_taken_over():
    """Before the integration shipped them, people imported them from GitHub into
    the same folder; with no record of ours, such a file is updated."""

    result = blueprint_files.plan({"a.yaml": b"version: 2\n"}, {"a.yaml": b"version: 1\n"}, {})

    assert result.write == ("a.yaml",)
    assert result.replaces is True


def test_a_deleted_blueprint_comes_back():
    record = {"a.yaml": blueprint_files.digest(b"version: 1\n")}

    result = blueprint_files.plan({"a.yaml": b"version: 1\n"}, {"a.yaml": None}, record)

    assert result.write == ("a.yaml",)
    assert result.replaces is False


def test_other_files_in_the_folder_are_not_touched(tmp_path):
    folder = _target(tmp_path)
    folder.mkdir(parents=True)
    (folder / "my_own.yaml").write_bytes(b"mine\n")

    blueprint_files.sync(str(tmp_path), {})

    assert (folder / "my_own.yaml").read_bytes() == b"mine\n"


OLD_IMPORT = (
    b"blueprint:\n  name: old\n  source_url: https://github.com/JustChr/BavarianData/"
    b"blob/main/blueprints/automation/bavariandata/arriving_home.yaml\n"
)


def _imported(config: Path) -> Path:
    return config.joinpath(*blueprint_files.IMPORTED)


def test_a_copy_imported_by_url_is_kept_current_too(tmp_path):
    """Home Assistant saves an import by URL under the GitHub user's folder, not
    ours, so that copy is a second one: it gets the same updates."""

    _imported(tmp_path).mkdir(parents=True)
    (_imported(tmp_path) / "arriving_home.yaml").write_bytes(OLD_IMPORT)
    shipped = (blueprint_files.SHIPPED / "arriving_home.yaml").read_bytes()

    result = blueprint_files.sync(str(tmp_path), {})

    assert (_imported(tmp_path) / "arriving_home.yaml").read_bytes() == shipped
    assert (_target(tmp_path) / "arriving_home.yaml").read_bytes() == shipped
    assert "JustChr/arriving_home.yaml" in result.write
    assert result.replaces is True
    assert result.record["JustChr/arriving_home.yaml"] == blueprint_files.digest(shipped)
    # Only what was imported: no other blueprint is added to that folder.
    assert [path.name for path in _imported(tmp_path).iterdir()] == ["arriving_home.yaml"]

    again = blueprint_files.sync(str(tmp_path), result.record)
    assert again.write == ()
    assert again.record == result.record


def test_an_imported_copy_changed_by_hand_is_left_alone(tmp_path):
    record = blueprint_files.sync(str(tmp_path), {}).record
    _imported(tmp_path).mkdir(parents=True)
    (_imported(tmp_path) / "arriving_home.yaml").write_bytes(OLD_IMPORT)
    record = blueprint_files.sync(str(tmp_path), record).record
    edited = (_imported(tmp_path) / "arriving_home.yaml").read_bytes() + b"# mine\n"
    (_imported(tmp_path) / "arriving_home.yaml").write_bytes(edited)

    result = blueprint_files.sync(str(tmp_path), record)

    assert result.write == ()
    assert (_imported(tmp_path) / "arriving_home.yaml").read_bytes() == edited


def test_a_namesake_from_another_repository_is_not_touched(tmp_path):
    _imported(tmp_path).mkdir(parents=True)
    other = b"blueprint:\n  source_url: https://github.com/JustChr/other/arriving_home.yaml\n"
    (_imported(tmp_path) / "arriving_home.yaml").write_bytes(other)

    result = blueprint_files.sync(str(tmp_path), {})

    assert (_imported(tmp_path) / "arriving_home.yaml").read_bytes() == other
    assert not any(key.startswith("JustChr/") for key in result.record)


def test_without_shipped_files_nothing_happens(tmp_path, monkeypatch):
    monkeypatch.setattr(blueprint_files, "SHIPPED", tmp_path / "missing")

    result = blueprint_files.sync(str(tmp_path), {"a.yaml": "x"})

    assert result.write == ()
    assert result.record == {"a.yaml": "x"}
    assert not _target(tmp_path).exists()
