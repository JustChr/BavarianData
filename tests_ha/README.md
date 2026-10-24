# tests_ha — tests against a real Home Assistant

`tests/` runs without Home Assistant, on purpose: it is fast, runs on Windows,
and covers everything that can be moved out of HA-importing code. What is left —
the config flow, entry setup and unload, the entity platforms, the services —
needs Home Assistant itself, and that is this suite, on
[`pytest-homeassistant-custom-component`](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component).

Home Assistant cannot be imported on Windows, so this suite is **Linux only**:

```bash
bash scripts/test_ha.sh                 # from Git Bash: re-runs itself in WSL
bash scripts/test_ha.sh -k config_flow  # pytest arguments pass through
bash scripts/test_ha.sh --cov           # with coverage of the integration
```

The venv lives in WSL's own home (`~/bd-ha/.venv`, Python 3.14) and is
created on first use. CI runs the suite in the **Home Assistant tests** job,
together with the main suite's coverage, and reports coverage per module
(`scripts/coverage_modules.py`) — the Quality Scale's `test-coverage` rule
counts every module on its own.

## Snapshots

`test_flow_snapshots.py` walks every setup and Configure screen and pins each
step as the frontend receives it, in `snapshots/test_flow_snapshots.ambr`
(HA's own syrupy extension). A screen that changes fails there with a diff.
When the change is intended, approve it and **read the diff** — that review is
the point:

```bash
bash scripts/test_ha.sh -k flow_snapshots --snapshot-update
```

## Rules

- **No BMW.** The plugin blocks sockets; a test that reaches one fails. BMW's
  REST API is answered at `api.py`'s two request functions (and the copy
  `container.py` imports), the device flow where `config_flow` imports it, the
  token refresh, the MQTT stream's start and stop, and the REST loops after
  setup (`test_setup.py`'s `no_bmw` fixture).
- **Only what needs HA.** Logic that can be tested without Home Assistant
  belongs in an HA-free module and in `tests/`; this suite is for the glue.
- **The 2026.3 floor.** Don't use an HA API newer than the minimum the
  integration supports, in tests either (see `test_setup.py`'s device lookup).
- `requirements_test_ha.txt` pins the plugin, which pins one Home Assistant
  release, and that release's frontend package; `test_requirements.py` checks
  the two agree.
