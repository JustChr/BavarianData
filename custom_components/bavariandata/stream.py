"""Handle BMW CarData MQTT streaming."""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import ssl
import time
from typing import Any, Awaitable, Callable, Coroutine, Optional

# ``paho-mqtt`` stays declared in ``manifest.json`` even though Home Assistant
# also ships it for its own ``mqtt`` integration. The official container image
# pre-installs every integration requirement, so it is already present there --
# but a Home Assistant Core (pip venv) install only installs a requirement when
# an integration that declares it is set up, and a user who never configures the
# built-in MQTT integration would have no paho at all. Core constrains the
# version to ``==2.1.0`` in ``package_constraints.txt``, which our ``>=2.1.0``
# resolves cleanly against, so declaring it can never pull a conflicting build.
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
        self._status_callback: Optional[Callable[[str, Optional[str]], Awaitable[None]]] = None
        self._reconnect_backoff = 5
        self._max_backoff = 300
        self._last_disconnect: Optional[float] = None
        self._disconnect_future: Optional[asyncio.Future[None]] = None
        self._retry_backoff = 3
        self._retry_task: Optional[asyncio.Task] = None
        # Bumped by every stop. A retry scheduled before a stop must not outlive
        # it, or a reload would end up with two streams for one account.
        self._stop_generation = 0
        # Set once by async_shutdown (unloading the entry); nothing connects after.
        self._closed = False
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

    async def async_shutdown(self) -> None:
        """Stop for good when the entry unloads: no retry may connect afterwards."""

        self._closed = True
        await self.async_stop()

    async def _async_connect_if_idle(self) -> None:
        """Connect, unless something else settled the stream while this waited.

        Every retry that opens a client goes through here, and the decision is
        taken under ``_connect_lock``, which every stop and credential update
        holds as well. A client another path already opened, a shutdown, or a
        refused login still waiting for new credentials all mean: do not connect.
        BMW allows one stream per account, so connecting anyway would open a
        second client. (A stop also cancels the pending retry, and a retry
        scheduled before a stop is dropped when it would be spawned.)
        """

        async with self._connect_lock:
            if self._closed or self._client is not None or self._awaiting_new_credentials:
                return
            await self._async_start_locked()

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
        self._stop_generation += 1
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

    def _run_coro(self, coro: Coroutine[Any, Any, Any]) -> "concurrent.futures.Future[Any]":
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
        # BMW's broker only negotiates TLS 1.3, so what has to be guarded against
        # is a handshake capped *below* it ("tlsv1 alert protocol version"), not
        # a weak floor. Hence a floor and never a ceiling: ``maximum_version`` is
        # deliberately left alone. The floor stays at the default 1.2 rather than
        # 1.3 -- raising it would gain nothing today and would lock us out if BMW
        # ever offers 1.2.
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
                self._schedule_retry()
                return
            _LOGGER.error("BMW MQTT connection failed: rc=%s", reason_code)
            self._run_coro(self._handle_unauthorized())
            client.loop_stop()
            self._client = None
            return
        elif self._status_callback:
            self._run_coro(self._status_callback("connection_failed", reason=str(reason_code)))

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
                    _LOGGER.debug("Ignoring transient MQTT rc=5; scheduling retry instead")
                self._schedule_retry()
                return
            self._run_coro(self._handle_unauthorized())
            self._reconnect_backoff = min(self._reconnect_backoff * 2, self._max_backoff)
            if self._status_callback:
                self._run_coro(self._status_callback("unauthorized", reason=reason))
        else:
            if should_reconnect:
                self._run_coro(self._async_reconnect(client))
            if self._status_callback:
                self._run_coro(self._status_callback("disconnected", reason=reason))

    async def _async_reconnect(self, client: Optional[mqtt.Client]) -> None:
        """Recover from ``client`` losing its connection."""

        async with self._connect_lock:
            if self._client is not None and self._client is not client:
                # This runs after paho's callback, by which time a credential
                # update may already have replaced the dropped client. Stopping
                # now would tear down the healthy connection that took its place.
                return
            # Release the dropped client (paho would otherwise reconnect it on
            # its own), then recover through the retry path like any failure.
            await self._async_stop_locked()
        self._schedule_retry()

    def _connect_failed(self, err: Exception) -> None:
        """A connection attempt failed; recovery has to go on regardless."""

        _LOGGER.error("BMW MQTT connection attempt failed: %s", err)
        self._schedule_retry()

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
            if not self._awaiting_new_credentials:
                return
            self._awaiting_new_credentials = False
            if self._client is not None:
                return

        async with self._connect_lock:
            if self._closed:
                return
            if self._client is not None:
                _LOGGER.debug("Updating MQTT credentials; reconnecting")
            # Stop and start as one step under the lock. The stop also ends any
            # pending retry, so no other attempt can open a client in between.
            await self._async_stop_locked()
            self._awaiting_new_credentials = False
            try:
                await self._async_start_locked()
            except Exception as err:
                self._connect_failed(err)

    async def async_update_token(self, id_token: Optional[str]) -> None:
        await self.async_update_credentials(id_token=id_token)

    def _cancel_retry(self) -> None:
        if self._retry_task and not self._retry_task.done():
            self._retry_task.cancel()
        self._retry_task = None
        self._retry_backoff = 3

    def _schedule_retry(self) -> None:
        """Schedule the next connection attempt. Safe to call from any thread."""

        # paho calls this from its network thread, so only the generation is
        # read here. The task is created on the event loop, where a stop that
        # happened in between has already bumped the generation.
        generation = self._stop_generation
        self.hass.loop.call_soon_threadsafe(self._spawn_retry_task, generation)

    def _spawn_retry_task(self, generation: int) -> None:
        # On the loop thread (the authoritative one): drop the retry if the
        # stream was stopped since it was scheduled, or another retry is already
        # pending. A stale retry must not take the slot of the next real one.
        if generation != self._stop_generation or (
            self._retry_task is not None and not self._retry_task.done()
        ):
            return
        delay = max(self._retry_backoff, self._min_reconnect_interval)
        self._retry_backoff = min(self._retry_backoff * 2, 30)
        self._last_disconnect = time.monotonic()
        # Registered against the config entry, so unloading cancels it.
        self._retry_task = self._config_entry.async_create_background_task(
            self.hass, self._async_retry(delay), f"{DOMAIN}_mqtt_retry"
        )

    async def _async_retry(self, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            if self._disconnect_future is not None and not self._disconnect_future.done():
                try:
                    await asyncio.wait_for(self._disconnect_future, timeout=10)
                except asyncio.TimeoutError:
                    if debug_enabled():
                        _LOGGER.debug(
                            "Timed out waiting for previous BMW MQTT disconnect before retry"
                        )
                finally:
                    self._disconnect_future = None
            await self._async_connect_if_idle()
        except asyncio.CancelledError:
            return
        except Exception as err:
            # A failed attempt must not end recovery; a stop ends the chain.
            self._connect_failed(err)
