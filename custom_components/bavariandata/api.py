"""Low-level async client for the BMW CarData REST API.

Centralizes authentication headers (``Authorization`` + required ``x-version``),
request timeouts and error handling for every CarData endpoint so individual
callers do not re-implement them. All endpoints are subject to BMW's daily quota
(50 requests / 24h) which is enforced separately by ``QuotaManager``.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from .const import API_BASE_URL, API_VERSION

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=30)

# Maximum number of pages to follow for paginated endpoints, guarding against a
# misbehaving ``nextToken`` loop.
MAX_PAGES = 50


class CardataApiError(Exception):
    """Raised when a CarData REST request fails."""

    def __init__(
        self,
        message: str,
        *,
        status: Optional[int] = None,
        error_id: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.error_id = error_id


def _auth_headers(access_token: str, *, accept: str = "application/json") -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "x-version": API_VERSION,
        "Accept": accept,
    }


def _extract_error_id(payload: Any) -> Optional[str]:
    """Pull a BMW ``CU-xxx`` error id out of an error body if present."""

    if isinstance(payload, dict):
        for key in ("errorId", "error_id", "code", "errorCode"):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith("CU-"):
                return value
    return None


async def async_request_json(
    session: aiohttp.ClientSession,
    method: str,
    path: str,
    access_token: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    json_body: Optional[Dict[str, Any]] = None,
) -> Any:
    """Perform an authenticated CarData request and return parsed JSON.

    Raises :class:`CardataApiError` for any non-success status or network error.
    """

    headers = _auth_headers(access_token)
    if json_body is not None:
        headers["Content-Type"] = "application/json"
    url = f"{API_BASE_URL}{path}"
    try:
        async with session.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_body,
            timeout=DEFAULT_TIMEOUT,
        ) as response:
            if response.status == 204:
                return {}
            text = await response.text()
            if response.status in (200, 201):
                if not text:
                    return {}
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return text
            try:
                error_payload: Any = json.loads(text) if text else None
            except json.JSONDecodeError:
                error_payload = text
            error_id = _extract_error_id(error_payload)
            raise CardataApiError(
                f"HTTP {response.status}"
                + (f" ({error_id})" if error_id else "")
                + f" for {method} {path}",
                status=response.status,
                error_id=error_id,
            )
    except aiohttp.ClientError as err:
        raise CardataApiError(f"Network error for {method} {path}: {err}") from err


async def async_request_bytes(
    session: aiohttp.ClientSession,
    method: str,
    path: str,
    access_token: str,
) -> Tuple[bytes, Optional[str]]:
    """Perform an authenticated request expecting a binary body (e.g. images)."""

    headers = _auth_headers(access_token, accept="*/*")
    url = f"{API_BASE_URL}{path}"
    try:
        async with session.request(
            method, url, headers=headers, timeout=DEFAULT_TIMEOUT
        ) as response:
            if response.status == 200:
                content_type = response.headers.get("Content-Type")
                return await response.read(), content_type
            text = await response.text()
            error_id = None
            try:
                error_id = _extract_error_id(json.loads(text)) if text else None
            except json.JSONDecodeError:
                pass
            raise CardataApiError(
                f"HTTP {response.status}"
                + (f" ({error_id})" if error_id else "")
                + f" for {method} {path}",
                status=response.status,
                error_id=error_id,
            )
    except aiohttp.ClientError as err:
        raise CardataApiError(f"Network error for {method} {path}: {err}") from err


async def _async_collect_paginated(
    session: aiohttp.ClientSession,
    path: str,
    access_token: str,
    *,
    base_params: Optional[Dict[str, Any]] = None,
    token_fields: Tuple[str, ...] = ("next_token", "nextToken"),
) -> Dict[str, Any]:
    """Follow ``nextToken`` pagination, accumulating the ``data`` array.

    Returns a dict ``{"data": [...all items...], "pages": <n>}``.
    """

    items: List[Any] = []
    params: Dict[str, Any] = dict(base_params or {})
    pages = 0
    next_token: Optional[str] = None
    while pages < MAX_PAGES:
        if next_token:
            params["nextToken"] = next_token
        payload = await async_request_json(session, "GET", path, access_token, params=params)
        pages += 1
        if isinstance(payload, dict):
            page_data = payload.get("data")
            if isinstance(page_data, list):
                items.extend(page_data)
            next_token = None
            for field in token_fields:
                value = payload.get(field)
                if isinstance(value, str) and value:
                    next_token = value
                    break
            if not next_token:
                break
        else:
            break
    return {"data": items, "pages": pages}


# --- High-level endpoint helpers -------------------------------------------------


async def async_get_vehicle_mappings(
    session: aiohttp.ClientSession, access_token: str
) -> Any:
    """GET /customers/vehicles/mappings"""

    return await async_request_json(
        session, "GET", "/customers/vehicles/mappings", access_token
    )


async def async_get_basic_data(
    session: aiohttp.ClientSession, access_token: str, vin: str
) -> Any:
    """GET /customers/vehicles/{vin}/basicData"""

    return await async_request_json(
        session, "GET", f"/customers/vehicles/{vin}/basicData", access_token
    )


async def async_get_telematic_data(
    session: aiohttp.ClientSession, access_token: str, vin: str, container_id: str
) -> Any:
    """GET /customers/vehicles/{vin}/telematicData?containerId="""

    return await async_request_json(
        session,
        "GET",
        f"/customers/vehicles/{vin}/telematicData",
        access_token,
        params={"containerId": container_id},
    )


async def async_get_charging_history(
    session: aiohttp.ClientSession,
    access_token: str,
    vin: str,
    *,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> Dict[str, Any]:
    """GET /customers/vehicles/{vin}/chargingHistory (paginated)."""

    params: Dict[str, Any] = {}
    if from_date:
        params["from"] = from_date
    if to_date:
        params["to"] = to_date
    return await _async_collect_paginated(
        session,
        f"/customers/vehicles/{vin}/chargingHistory",
        access_token,
        base_params=params,
    )


async def async_get_tyre_diagnosis(
    session: aiohttp.ClientSession, access_token: str, vin: str
) -> Any:
    """GET /customers/vehicles/{vin}/smartMaintenanceTyreDiagnosis"""

    return await async_request_json(
        session,
        "GET",
        f"/customers/vehicles/{vin}/smartMaintenanceTyreDiagnosis",
        access_token,
    )


async def async_get_location_based_charging_settings(
    session: aiohttp.ClientSession, access_token: str, vin: str
) -> Dict[str, Any]:
    """GET /customers/vehicles/{vin}/locationBasedChargingSettings (paginated)."""

    return await _async_collect_paginated(
        session,
        f"/customers/vehicles/{vin}/locationBasedChargingSettings",
        access_token,
    )


async def async_get_vehicle_image(
    session: aiohttp.ClientSession, access_token: str, vin: str
) -> Tuple[bytes, Optional[str]]:
    """GET /customers/vehicles/{vin}/image -> (bytes, content_type)."""

    return await async_request_bytes(
        session, "GET", f"/customers/vehicles/{vin}/image", access_token
    )
