"""Handle BMW CarData MQTT streaming."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import ssl
import time
from typing import Any, Awaitable, Callable, Coroutine, Optional

import paho.mqtt.client as mqtt

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .debug import debug_enabled

_LOGGER = logging.getLogger(__name__)

# BMW's MQTTv3 CONNACK/DISCONNECT numeric codes are surfaced by paho 2.x's
# VERSION2 callbacks as their MQTTv5 equivalents: 4 ("bad user name or
# password") -> 134 and 5 ("not authorized") -> 135. A clean disconnect is 0.
_RC_BAD_CREDENTIALS = 134
_RC_NOT_AUTHORIZED = 135


def _log_future_exception(future: concurrent.futures.Future) -> None:
    """Surface exceptions from coroutines scheduled off the MQTT network thread.

    ``run_coroutine_threadsafe`` hands back a future nobody awaits, so without
    this a regression in the message callback -- or any other scheduled
    coroutine -- would fail completely silently. This runs on the event loop
    once the coroutine finishes.
    """

    if future.cancelled():
        return
    exc = future.exception()
    if exc is not None:
        _LOGGER.error("BMW CarData stream task failed: %s", exc, exc_info=exc)


class CardataStreamManager:
    """Manage the MQTT connection to BMW CarData."""

    def __init__(
        self,
        *,
        hass: HomeAssistant,
        client_id: str,
        gcid: str,
        id_token: str,
        host: str,
        port: int,
        keepalive: int,
        config_entry: ConfigEntry,
        error_callback: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> None:
        self.hass = hass
        self._config_entry = config_entry
        self._client_id = client_id
        self._gcid = gcid
        self._password = id_token
        self._host = host
        self._port = port
        self._keepalive = keepalive
        self._client: Optional[mqtt.Client] = None
        self._message_callback: Optional[Callable[[dict], Awaitable[None]]] = None
        self._error_callback = error_callback
        self._reauth_notified = False
        self._unauthorized_retry_in_progress = False
        self._awaiting_new_credentials = False
        self._status_callback: Optional[
            Callable[[str, Optional[str]], Awaitable[None]]
        ] = None
        self._reconnect_backoff = 5
        self._max_backoff = 300
        self._last_disconnect: Optional[float] = None
        self._disconnect_future: Optional[asyncio.Future[None]] = None
        self._retry_backoff = 3
        self._retry_task: Optional[asyncio.Task] = None
        self._min_reconnect_interval = 10.0
        self._connect_lock = asyncio.Lock()

    async def async_start(self) -> None:
        async with self._connect_lock:
            await self._async_start_locked()

    async def _async_start_locked(self) -> None:
        self._disconnect_future = None
        if self._last_disconnect is not None:
            elapsed = time.monotonic() - self._last_disconnect
            delay = self._min_reconnect_interval - elapsed
            if delay > 0:
                if debug_enabled():
                    _LOGGER.debug(
                        "Waiting %.1fs before starting BMW MQTT client",
                        delay,
                    )
                await asyncio.sleep(delay)
        await self.hass.async_add_executor_job(self._start_client)
        self._reconnect_backoff = 5

    async def async_stop(self) -> None:
        async with self._connect_lock:
            await self._async_stop_locked()

    async def _async_stop_locked(self) -> None:
        disconnect_future: Optional[asyncio.Future[None]] = None
        client = self._client
        self._client = None
        if client is not None:
            loop = asyncio.get_running_loop()
            disconnect_future = loop.create_future()
            self._disconnect_future = disconnect_future
            userdata = getattr(client, "_userdata", None)
            if isinstance(userdata, dict):
                userdata["reconnect"] = False
            try:
                client.disconnect()
            except Exception as err:  # pragma: no cover - defensive logging
                if debug_enabled():
                    _LOGGER.debug("Error disconnecting BMW MQTT client: %s", err)
            if disconnect_future is not None:
                try:
                    await asyncio.wait_for(disconnect_future, timeout=5)
                except asyncio.TimeoutError:
                    if debug_enabled():
                        _LOGGER.debug("Timeout waiting for BMW MQTT disconnect acknowledgement")
                finally:
                    self._disconnect_future = None
            try:
                client.loop_stop()
            except Exception as err:  # pragma: no cover - defensive logging
                if debug_enabled():
                    _LOGGER.debug("Error stopping BMW MQTT loop: %s", err)
        self._last_disconnect = time.monotonic()
        self._cancel_retry()

    @property
    def client(self) -> Optional[mqtt.Client]:
        return self._client

    def set_message_callback(self, callback: Callable[[dict], Awaitable[None]]) -> None:
        self._message_callback = callback

    def set_status_callback(
        self, callback: Callable[[str, Optional[str]], Awaitable[None]]
    ) -> None:
        self._status_callback = callback

    def _run_coro(
        self, coro: Coroutine[Any, Any, Any]
    ) -> "concurrent.futures.Future[Any]":
        """Schedule a coroutine from the MQTT thread and log any exception.

        paho's callbacks run on its own network thread, so everything that
        touches the event loop is bounced across with
        ``run_coroutine_threadsafe``. Attaching a done-callback is what keeps a
        failure in (say) ``_message_callback`` from vanishing unnoticed.
        """

        future = asyncio.run_coroutine_threadsafe(coro, self.hass.loop)
        future.add_done_callback(_log_future_exception)
        return future

    @property
    def debug_info(self) -> dict[str, str | int | bool]:
        """Return connection parameters for diagnostics."""

        # NOTE: never expose the id_token here. This dict is surfaced as
        # Home Assistant entity attributes (see CardataDiagnosticsSensor), so any
        # secret placed here becomes readable from the UI/state machine.
        return {
            "client_id": self._client_id,
            "gcid": self._gcid,
            "host": self._host,
            "port": self._port,
            "keepalive": self._keepalive,
            "topic": f"{self._gcid}/+",
            "clean_session": True,
            "protocol": "MQTTv311",
            "id_token_present": bool(self._password),
        }

    def _start_client(self) -> None:
        client_id = self._gcid
        client = mqtt.Client(
            # paho 2.x defaults to the deprecated VERSION1 callback API and warns;
            # VERSION2 is the supported one and the only one paho 3.x will ship.
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            clean_session=True,
            # Subscribe only to direct VIN topics. Do not modify this unless BMW changes the stream contract.
            userdata={"topic": f"{self._gcid}/+"},
            protocol=mqtt.MQTTv311,
            transport="tcp",
        )
        if debug_enabled():
            _LOGGER.debug(
                "Initializing MQTT client: client_id=%s host=%s port=%s",
                client_id,
                self._host,
                self._port,
            )
        client.username_pw_set(username=self._gcid, password=self._password)
        if debug_enabled():
            _LOGGER.debug(
                "MQTT credentials set for GCID %s (token length=%s)",
                self._gcid,
                len(self._password or ""),
            )
        client.on_connect = self._handle_connect
        client.on_subscribe = self._handle_subscribe
        client.on_message = self._handle_message
        client.on_disconnect = self._handle_disconnect
        context = ssl.create_default_context()
        # BMW's broker requires TLS 1.3 — a handshake capped at 1.2 is rejected
        # with "tlsv1 alert protocol version". Set a floor, never a ceiling.
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        client.tls_set_context(context)
        client.tls_insecure_set(False)
        client.reconnect_delay_set(min_delay=5, max_delay=60)

        try:
            client.connect(self._host, self._port, keepalive=self._keepalive)
        except Exception as err:
            _LOGGER.error("Unable to connect to BMW MQTT: %s", err)
            client.loop_stop()
            raise
        client.loop_start()
        self._client = client

    def _handle_connect(
        self, client: mqtt.Client, userdata, flags, reason_code, properties=None
    ) -> None:
        if reason_code == 0:
            topic = userdata.get("topic")
            if topic:
                result = client.subscribe(topic)
                if debug_enabled():
                    _LOGGER.debug("Subscribed to %s result=%s", topic, result)
            if self._reauth_notified:
                self._reauth_notified = False
                self._awaiting_new_credentials = False
                self._run_coro(self._notify_recovered())
            self._cancel_retry()
            self._last_disconnect = None
            self._retry_backoff = 3
            if self._status_callback:
                self._run_coro(self._status_callback("connected"))
        elif reason_code.value in (_RC_BAD_CREDENTIALS, _RC_NOT_AUTHORIZED):
            now = time.monotonic()
            if (
                reason_code.value == _RC_NOT_AUTHORIZED
                and self._last_disconnect is not None
                and now - self._last_disconnect < 10
            ):
                if debug_enabled():
                    _LOGGER.debug(
                        "BMW MQTT connection refused shortly after disconnect; scheduling retry"
                    )
                client.loop_stop(force=True)
                self._client = None
                self._schedule_retry(3)
                return
            _LOGGER.error("BMW MQTT connection failed: rc=%s", reason_code)
            self._run_coro(self._handle_unauthorized())
            client.loop_stop()
            self._client = None
            return
        elif self._status_callback:
            self._run_coro(
                self._status_callback("connection_failed", reason=str(reason_code))
            )

    def _handle_subscribe(
        self, client: mqtt.Client, userdata, mid, reason_code_list, properties=None
    ) -> None:
        if debug_enabled():
            _LOGGER.debug("BMW MQTT subscribed mid=%s qos=%s", mid, reason_code_list)

    def _handle_message(self, client: mqtt.Client, userdata, msg: mqtt.MQTTMessage) -> None:
        payload = msg.payload.decode(errors="ignore")
        if debug_enabled():
            _LOGGER.debug("BMW MQTT message on %s: %s", msg.topic, payload)
        if not self._message_callback:
            return
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return
        if self._message_callback:
            self._run_coro(self._message_callback(data))

    def _handle_disconnect(
        self, client: mqtt.Client, userdata, disconnect_flags, reason_code, properties=None
    ) -> None:
        reason = str(reason_code)
        is_clean = reason_code == 0
        # A clean, self-initiated disconnect (e.g. reconnect on credential
        # refresh) is routine, so keep it at debug. Anything else is unexpected.
        if is_clean:
            if debug_enabled():
                _LOGGER.debug("BMW MQTT disconnected rc=%s (%s)", reason_code, reason)
        else:
            _LOGGER.warning("BMW MQTT disconnected rc=%s (%s)", reason_code, reason)
        self._last_disconnect = time.monotonic()
        disconnect_future = self._disconnect_future
        if disconnect_future and not disconnect_future.done():
            def _set_disconnect() -> None:
                if not disconnect_future.done():
                    disconnect_future.set_result(None)

            self.hass.loop.call_soon_threadsafe(_set_disconnect)
        should_reconnect = True
        if isinstance(userdata, dict):
            should_reconnect = userdata.get("reconnect", True)
            userdata["reconnect"] = True
        if reason_code.value in (_RC_BAD_CREDENTIALS, _RC_NOT_AUTHORIZED):
            now = time.monotonic()
            if (
                reason_code.value == _RC_NOT_AUTHORIZED
                and self._last_disconnect is not None
                and now - self._last_disconnect < 10
            ):
                if debug_enabled():
                    _LOGGER.debug(
                        "Ignoring transient MQTT rc=5; scheduling retry instead"
                    )
                self._schedule_retry(3)
                return
            self._run_coro(self._handle_unauthorized())
            self._reconnect_backoff = min(self._reconnect_backoff * 2, self._max_backoff)
            if self._status_callback:
                self._run_coro(self._status_callback("unauthorized", reason=reason))
        else:
            if should_reconnect:
                self._run_coro(self._async_reconnect())
            if self._status_callback:
                self._run_coro(self._status_callback("disconnected", reason=reason))

    async def _async_reconnect(self) -> None:
        await self.async_stop()
        await asyncio.sleep(self._reconnect_backoff)
        try:
            await self.async_start()
        except Exception as err:
            _LOGGER.error("BMW MQTT reconnect failed: %s", err)
            self._reconnect_backoff = min(self._reconnect_backoff * 2, self._max_backoff)
        else:
            self._reconnect_backoff = 5

    async def _handle_unauthorized(self) -> None:
        if self._unauthorized_retry_in_progress:
            return
        self._unauthorized_retry_in_progress = True
        try:
            self._awaiting_new_credentials = True
            if not self._reauth_notified:
                self._reauth_notified = True
                await self._notify_error("unauthorized")
            else:
                await self.async_stop()
            if self._status_callback:
                await self._status_callback("unauthorized", reason="MQTT rc=5")
        finally:
            self._unauthorized_retry_in_progress = False

    async def _notify_error(self, reason: str) -> None:
        await self.async_stop()
        if self._error_callback:
            await self._error_callback(reason)

    async def _notify_recovered(self) -> None:
        if self._error_callback:
            await self._error_callback("recovered")

    async def async_update_credentials(
        self,
        *,
        gcid: Optional[str] = None,
        id_token: Optional[str] = None,
    ) -> None:
        if not gcid and not id_token:
            return

        reconnect_required = False

        if gcid and gcid != self._gcid:
            _LOGGER.debug("Updating MQTT GCID from %s to %s", self._gcid, gcid)
            self._gcid = gcid
            reconnect_required = True

        if id_token and id_token != self._password:
            self._password = id_token
            reconnect_required = True

        if not reconnect_required:
            if self._awaiting_new_credentials:
                self._awaiting_new_credentials = False
                if self._client is None:
                    try:
                        await self.async_start()
                    except Exception as err:
                        _LOGGER.error(
                            "BMW MQTT reconnect failed after credential refresh: %s",
                            err,
                        )
            return

        if self._client:
            _LOGGER.debug("Updating MQTT credentials; reconnecting")
            await self.async_stop()

        self._reconnect_backoff = 5
        if self._awaiting_new_credentials:
            self._awaiting_new_credentials = False

        delay = 0.0
        if self._last_disconnect is not None:
            elapsed = time.monotonic() - self._last_disconnect
            if elapsed < 2.0:
                delay = 2.0 - elapsed
        if delay > 0:
            await asyncio.sleep(delay)

        try:
            await self.async_start()
        except Exception as err:
            _LOGGER.error("BMW MQTT reconnect failed after credential update: %s", err)

    async def async_update_token(self, id_token: Optional[str]) -> None:
        await self.async_update_credentials(id_token=id_token)

    def _cancel_retry(self) -> None:
        if self._retry_task and not self._retry_task.done():
            self._retry_task.cancel()
        self._retry_task = None
        self._retry_backoff = 3

    def _schedule_retry(self, delay: float) -> None:
        if self._retry_task is not None and not self._retry_task.done():
            return

        delay = max(delay, self._retry_backoff, self._min_reconnect_interval)
        self._retry_backoff = min(self._retry_backoff * 2, 30)
        self._last_disconnect = time.monotonic()

        async def _retry() -> None:
            try:
                await asyncio.sleep(delay)
                if self._client is None:
                    if (
                        self._disconnect_future is not None
                        and not self._disconnect_future.done()
                    ):
                        try:
                            await asyncio.wait_for(self._disconnect_future, timeout=10)
                        except asyncio.TimeoutError:
                            if debug_enabled():
                                _LOGGER.debug(
                                    "Timed out waiting for previous BMW MQTT disconnect before retry"
                                )
                        finally:
                            self._disconnect_future = None
                    async with self._connect_lock:
                        await self._async_start_locked()
            except asyncio.CancelledError:
                return
            except Exception as err:  # pragma: no cover - defensive logging
                _LOGGER.error("BMW MQTT retry failed: %s", err)
            finally:
                self._retry_task = None

        # _schedule_retry runs on paho's network thread; hop to the event loop
        # to create the task so it is registered against the config entry
        # (cancelled on unload) and its exceptions surface via Home Assistant.
        self.hass.loop.call_soon_threadsafe(self._spawn_retry_task, _retry())

    def _spawn_retry_task(self, coro: Coroutine[Any, Any, Any]) -> None:
        # Re-check on the loop thread (the authoritative one) so two disconnects
        # racing in from the network thread can't spawn duplicate retries.
        if self._retry_task is not None and not self._retry_task.done():
            coro.close()
            return
        self._retry_task = self._config_entry.async_create_background_task(
            self.hass, coro, f"{DOMAIN}_mqtt_retry"
        )
