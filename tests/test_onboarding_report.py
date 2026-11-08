"""The activator says Home Assistant will continue only when this setup heard it.

Each setup run hands the activator its own one-time webhook, and the bookmark
carries that address. A bookmark kept from an earlier run reports to a webhook
that no longer exists, and Home Assistant answers an unregistered webhook with
an empty 200 (it never reveals which ids exist). The activator used to take any
answer as delivered, so the portal said "Home Assistant will continue
automatically" while the setup dialog waited on (Discussion #293). It now counts
a report as delivered only when the answer carries ``REPORT_ACK``, which only
this integration's own webhook handler returns.

Runs the generated activator under Node with the portal's endpoints and the
webhook stubbed, like the card tests: the report is behaviour across fetches.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from .conftest import load_module

OB = load_module("onboarding")

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")

WEBHOOK = "https://ha.example/api/webhook/token"

# The portal page the bookmark is clicked on, and a portal that already has
# every wanted field on (as in #293: nothing to add, only the report to send).
_HARNESS = r"""
const fs = require("fs");
const [src, webhookAnswer] = [fs.readFileSync(process.argv[2], "utf8"), process.argv[3] || ""];
globalThis.location = { origin: "https://www.bmw.co.uk", hostname: "www.bmw.co.uk",
  pathname: "/en-gb/mybmw/mapped-vehicle/MV1/cardata-streaming" };
const box = { style: {}, innerHTML: "", querySelector: () => ({}) };
globalThis.document = { createElement: () => box, body: { appendChild() {} } };
const reply = (body) => ({ status: 200, ok: true,
  json: async () => body, text: async () => JSON.stringify(body) });
globalThis.fetch = async (url) => {
  if (url.startsWith("https://ha.example/")) {
    return { status: 200, ok: true, json: async () => null, text: async () => webhookAnswer };
  }
  if (url.includes("/applications")) return reply({ data: [] });
  if (url.includes("/mapping-details")) return reply({ data: {} });
  if (url.includes("/catalogue")) return reply({ data: { items: [{ id: "a.b", streamable: true }] } });
  if (url.includes("/streams/")) return reply({ data: { attributes: ["a.b"] } });
  return reply(null);
};
eval(src).then(() => process.stdout.write(JSON.stringify(box.innerHTML)));
"""


def _toast(tmp_path, webhook_answer: str) -> str:
    js = OB.build_activator_js(["a.b"], report_url=WEBHOOK)
    script = tmp_path / "activator.js"
    script.write_text(js, encoding="utf-8")
    harness = tmp_path / "harness.js"
    harness.write_text(_HARNESS, encoding="utf-8")
    run = subprocess.run(
        [NODE, str(harness), str(script), webhook_answer],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return json.loads(run.stdout)


def test_a_report_this_setup_received_lets_home_assistant_continue(tmp_path) -> None:
    toast = _toast(tmp_path, OB.REPORT_ACK)

    assert "Home Assistant will continue automatically" in toast
    assert "earlier setup" not in toast


def test_a_bookmark_from_an_earlier_setup_says_so_instead_of_claiming_success(
    tmp_path,
) -> None:
    # Home Assistant's answer to a webhook id nobody registered: 200, no body.
    toast = _toast(tmp_path, "")

    assert "Home Assistant will continue automatically" not in toast
    assert "earlier setup" in toast
    assert "Copy this and paste it into Home Assistant" in toast
