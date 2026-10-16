"""Helpers for managing BMW CarData containers."""

from __future__ import annotations

import asyncio
import logging
import hashlib
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

import aiohttp

from .api import CardataApiError, async_request_json
from .const import (
    HV_BATTERY_CONTAINER_NAME,
    HV_BATTERY_CONTAINER_PURPOSE,
    HV_BATTERY_DESCRIPTORS,
)
from .debug import debug_enabled
from .descriptors import container_descriptors

_LOGGER = logging.getLogger(__name__)


class CardataContainerError(Exception):
    """Raised when BMW CarData container management fails."""

    def __init__(
        self, message: str, *, status: Optional[int] = None, error_id: Optional[str] = None
    ) -> None:
        super().__init__(message)
        self.status = status
        # BMW's ``CU-xxx`` code, e.g. CU-124 for "maximum number of containers".
        self.error_id = error_id


class CardataContainerManager:
    """Ensure containers required for the integration exist."""

    def __init__(
        self,
        *,
        session: aiohttp.ClientSession,
        entry_id: str,
        initial_container_id: Optional[str] = None,
        claim: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        self._session = session
        self._entry_id = entry_id
        self._claim = claim
        self._container_id: Optional[str] = initial_container_id
        self._lock = asyncio.Lock()
        # Everything BMW cannot stream (the container is those fields' only route
        # into HA) plus the battery keys, so a fresh install shows values before
        # the first stream message. One GET returns the lot.
        descriptors = container_descriptors(HV_BATTERY_DESCRIPTORS)
        self._desired_descriptors = tuple(descriptors)
        self._descriptor_signature = self.compute_signature(descriptors)

    @property
    def container_id(self) -> Optional[str]:
        """Return the currently known container identifier."""

        return self._container_id

    @property
    def descriptor_signature(self) -> str:
        """Return the signature for the desired descriptor set."""

        return self._descriptor_signature

    @staticmethod
    def compute_signature(descriptors: Iterable[str]) -> str:
        """Return a stable signature for a descriptor collection."""

        normalized = sorted(dict.fromkeys(descriptors))
        joined = "|".join(normalized)
        return hashlib.sha1(joined.encode("utf-8")).hexdigest()

    def sync_from_entry(self, container_id: Optional[str]) -> None:
        """Synchronize the known container id with stored config data."""

        self._container_id = container_id

    async def async_ensure_hv_container(self, access_token: Optional[str]) -> Optional[str]:
        """Ensure the HV battery container exists and is active."""

        if not access_token:
            if debug_enabled():
                _LOGGER.debug(
                    "[%s] Skipping container ensure because access token is missing",
                    self._entry_id,
                )
            return self._container_id

        async with self._lock:
            if self._container_id:
                if debug_enabled():
                    _LOGGER.debug(
                        "[%s] Using cached HV container %s without validation",
                        self._entry_id,
                        self._container_id,
                    )
                return self._container_id

            created_id = await self._create_container(access_token)
            self._container_id = created_id
            _LOGGER.info("[%s] Created HV battery container %s", self._entry_id, created_id)
            return self._container_id

    async def async_reset_hv_container(self, access_token: Optional[str]) -> Optional[str]:
        """Delete existing HV telemetry containers and create a fresh one."""

        if not access_token:
            if debug_enabled():
                _LOGGER.debug(
                    "[%s] Skipping container reset because access token is missing",
                    self._entry_id,
                )
            return self._container_id

        async with self._lock:
            containers = await self._list_containers(access_token)

            # Create before deleting. The reverse order would leave the entry
            # with no container at all if BMW rejects the new one (e.g. a
            # descriptor it no longer accepts), and the old one is still a
            # working fallback until the replacement exists.
            new_id = await self._create_container(access_token)
            self._container_id = new_id
            _LOGGER.info(
                "[%s] Reset HV telemetry container; new container id %s",
                self._entry_id,
                new_id,
            )

            deleted_ids: List[str] = []
            for container in containers:
                container_id = container.get("containerId")
                if not isinstance(container_id, str) or container_id == new_id:
                    continue
                if not self._matches_hv_container(container):
                    continue
                try:
                    await self._delete_container(access_token, container_id)
                except CardataContainerError as err:
                    _LOGGER.warning(
                        "[%s] Failed to delete container %s: %s",
                        self._entry_id,
                        container_id,
                        err,
                    )
                    continue
                deleted_ids.append(container_id)

            if deleted_ids and debug_enabled():
                _LOGGER.debug(
                    "[%s] Deleted %s stale container(s): %s",
                    self._entry_id,
                    len(deleted_ids),
                    ", ".join(deleted_ids),
                )

            return new_id

    async def _create_container(self, access_token: str) -> str:
        payload = {
            "name": HV_BATTERY_CONTAINER_NAME,
            "purpose": HV_BATTERY_CONTAINER_PURPOSE,
            "technicalDescriptors": list(self._desired_descriptors),
        }
        response = await self._request(
            "POST", "/customers/containers", access_token, json_body=payload
        )
        container_id = response.get("containerId") if isinstance(response, dict) else None
        if not container_id:
            raise CardataContainerError("Container creation response missing containerId")
        return container_id

    async def _list_containers(self, access_token: str) -> List[Dict[str, Any]]:
        response = await self._request("GET", "/customers/containers", access_token)
        if isinstance(response, list):
            containers = [item for item in response if isinstance(item, dict)]
        elif isinstance(response, dict):
            possible = response.get("containers")
            if isinstance(possible, list):
                containers = [item for item in possible if isinstance(item, dict)]
            else:
                containers = []
        else:
            containers = []
        return containers

    def _matches_hv_container(self, container: Dict[str, Any]) -> bool:
        if not isinstance(container, dict):
            return False
        purpose = container.get("purpose")
        name = container.get("name")
        descriptors = container.get("technicalDescriptors")
        signature = None
        if isinstance(descriptors, list):
            signature = self.compute_signature(
                [item for item in descriptors if isinstance(item, str)]
            )
        return any(
            [
                isinstance(purpose, str) and purpose == HV_BATTERY_CONTAINER_PURPOSE,
                isinstance(name, str) and name == HV_BATTERY_CONTAINER_NAME,
                signature == self._descriptor_signature,
            ]
        )

    async def _delete_container(self, access_token: str, container_id: str) -> None:
        try:
            await self._request(
                "DELETE",
                f"/customers/containers/{container_id}",
                access_token,
            )
        except CardataContainerError as err:
            if err.status == 404:
                if debug_enabled():
                    _LOGGER.debug(
                        "[%s] Container %s already deleted",
                        self._entry_id,
                        container_id,
                    )
                return
            raise

    async def _request(
        self,
        method: str,
        path: str,
        access_token: str,
        *,
        json_body: Optional[Dict[str, Any]] = None,
    ) -> Any:
        # Container calls count against the daily quota like any other
        # CarData request: BMW's limit covers "the use of the CarData APIs".
        if self._claim is not None:
            try:
                await self._claim()
            except Exception as err:  # the quota manager's own error type
                raise CardataContainerError(f"Request not sent: {err}") from err
        if debug_enabled():
            _LOGGER.debug("[%s] %s %s", self._entry_id, method, path)
        try:
            return await async_request_json(
                self._session, method, path, access_token, json_body=json_body
            )
        except CardataApiError as err:
            raise CardataContainerError(str(err), status=err.status, error_id=err.error_id) from err
