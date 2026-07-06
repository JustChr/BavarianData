"""Test fixtures / fakes for the BMW CarData integration.

These tests intentionally avoid importing Home Assistant so the core API/auth
helpers can be unit-tested in a lightweight environment. A minimal fake aiohttp
session is provided to drive ``api.py`` without real network access.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import pathlib
import sys
import types
from typing import Any, Dict, List, Optional

_PKG = "custom_components.bavariandata"
_BASE = pathlib.Path(__file__).resolve().parents[1] / "custom_components" / "bavariandata"


def _ensure_synthetic_package() -> None:
    """Register a synthetic package so HA-free submodules import in isolation.

    The real ``custom_components/bavariandata/__init__.py`` pulls in Home
    Assistant. We register lightweight namespace stand-ins with the correct
    ``__path__`` so ``from .const import ...`` style relative imports resolve to
    the real source files without executing the heavy package init.
    """

    if "custom_components" not in sys.modules:
        cc = types.ModuleType("custom_components")
        cc.__path__ = [str(_BASE.parent)]
        sys.modules["custom_components"] = cc
    if _PKG not in sys.modules:
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [str(_BASE)]
        sys.modules[_PKG] = pkg


def load_module(name: str):
    """Load ``custom_components.bavariandata.<name>`` in isolation."""

    _ensure_synthetic_package()
    full = f"{_PKG}.{name}"
    if full in sys.modules:
        return sys.modules[full]
    spec = importlib.util.spec_from_file_location(full, _BASE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, status: int, body: Any, headers: Optional[Dict[str, str]] = None):
        self.status = status
        self._body = body
        self.headers = headers or {}

    async def __aenter__(self) -> "FakeResponse":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def text(self) -> str:
        if isinstance(self._body, (dict, list)):
            return json.dumps(self._body)
        if self._body is None:
            return ""
        if isinstance(self._body, bytes):
            return self._body.decode("utf-8", "ignore")
        return str(self._body)

    async def read(self) -> bytes:
        if isinstance(self._body, bytes):
            return self._body
        return (await self.text()).encode("utf-8")


class FakeSession:
    """Records requests and replays a queue of FakeResponse objects."""

    def __init__(self, responses: List[FakeResponse]):
        self._responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self._responses.pop(0)
