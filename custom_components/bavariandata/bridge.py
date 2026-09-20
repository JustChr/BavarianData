"""Publish the car onto Home Assistant's MQTT broker for a charge controller.

The Home Assistant half of the evcc / wallbox bridge; the topic layout, the
payloads and the generated evcc config live in the HA-free ``evcc.py`` so they
can be unit-tested.

**Why Home Assistant's own MQTT integration, and not a broker of our own.**
Anyone bridging to evcc or openWB already runs a broker, and Home Assistant is
already connected to it -- so publishing through ``mqtt.async_publish`` lands on
exactly the broker the charge controller reads, with no host, port, TLS setting
or password for us to ask for, store, redact from diagnostics and get wrong. The
cost is one prerequisite ("set up the MQTT integration"), which the options
screen states outright instead of failing quietly.

Two properties this module exists to guarantee:

- **Retained, and republished on a heartbeat.** A charge controller that starts
  after Home Assistant must find the SoC waiting for it, and evcc's plugins
  treat a value that stops arriving as stale -- but a parked car says nothing for
  days, and its SoC is no less true for that. So values are retained *and*
  re-sent every ``REPUBLISH_INTERVAL_S`` even when unchanged, and the generated
  evcc config sets no timeout.
- **Nothing lingers that we no longer stand behind.** A value that becomes
  unknown, a vehicle that goes away, the bridge being switched off: each clears
  the retained topics it owns. Otherwise a charge controller would keep charging
  against a frozen SoC from last week, which is the one genuinely dangerous
  failure mode of pushing data to something that acts on it.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_call_later

from .debug import debug_enabled
from .evcc import (
    ALL_TOPICS,
    BridgeConfig,
    BridgeSnapshot,
    bridge_payloads,
    bridge_topics,
    normalize_prefix,
)

_LOGGER = logging.getLogger(__name__)

# Descriptor messages arrive in bursts -- a charging car can emit a dozen in a
# second -- and each one would otherwise trigger its own publish cycle. Coalesced
# into one publish shortly after the burst settles: a two-second lag is nothing
# to a charge controller that re-evaluates every ten.
PUBLISH_DEBOUNCE_S = 2

# How often everything is re-sent unchanged, so a charge controller's staleness
# check never blanks the SoC of a car that is merely parked. Cheap: a handful of
# short messages to a local broker.
REPUBLISH_INTERVAL_S = 300

# An empty payload published *retained* is how MQTT deletes a retained message.
# Not a value -- brokers treat it as "forget this topic" -- which is exactly the
# semantics wanted for "we no longer know this".
_CLEAR = ""


async def async_clear_published(hass: HomeAssistant, *, prefix: str, vins: Iterable[str]) -> None:
    """Delete every retained topic the bridge owns for these vehicles.

    A module-level function, not a method, because the most important caller has
    no bridge left to call: removing the integration happens after the entry has
    unloaded. Retained messages outlive us by design, so removing BavarianData
    while the bridge was on would otherwise leave a broker serving a state of
    charge forever -- and evcc happily charging against it.
    """

    for vin in vins:
        if not vin:
            continue
        for suffix in ALL_TOPICS:
            topic = f"{normalize_prefix(prefix)}/{vin}/{suffix}"
            try:
                # Always retained: a non-retained empty message deletes nothing.
                await mqtt.async_publish(hass, topic, _CLEAR, qos=0, retain=True)
            except HomeAssistantError as err:
                _LOGGER.debug("[bridge] could not clear %s: %s", topic, err)
            except Exception:  # noqa: BLE001 - teardown must not fail on MQTT
                _LOGGER.exception("[bridge] unexpected error clearing %s", topic)


class VehicleBridge:
    """Mirrors each vehicle's live state onto the broker, and keeps it honest."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: Any,
        *,
        config: BridgeConfig,
    ) -> None:
        self.hass = hass
        self._coordinator = coordinator
        self._config = config
        # What the broker currently holds, per VIN: topic suffix -> payload. The
        # diff against this is what keeps a burst of identical values from
        # becoming a burst of publishes.
        self._published: dict[str, dict[str, str]] = {}
        # The prefix each VIN's data was published under. Kept separately from
        # the live config because changing the prefix has to clean up the *old*
        # topics, and by then the config already says the new one.
        self._prefixes: dict[str, str] = {}
        self._timers: dict[str, Any] = {}
        self._warned_unavailable = False
        self._closed = False

    @property
    def config(self) -> BridgeConfig:
        return self._config

    @property
    def enabled(self) -> bool:
        return self._config.enabled and not self._closed

    def available(self) -> bool:
        """Whether Home Assistant has a working MQTT integration to publish via.

        Checked rather than assumed: the bridge is useless without it, and the
        difference between "no broker" and "no data yet" is the first thing
        anyone debugging this needs to know.
        """

        return any(
            entry.state is ConfigEntryState.LOADED
            for entry in self.hass.config_entries.async_entries("mqtt")
        )

    # --- lifecycle ---------------------------------------------------------

    @callback
    def apply_config(self, config: BridgeConfig) -> None:
        """Adopt changed settings without reloading the config entry.

        A reload would tear down and rebuild the stream, and BMW allows one
        concurrent stream per account -- so every options screen in this
        integration applies in place instead. The "have we warned about MQTT"
        latch is released too: the user has just been through the settings and
        may well have fixed exactly that.
        """

        self._config = config
        self._warned_unavailable = False

    @callback
    def async_schedule(self, vin: Optional[str] = None) -> None:
        """Queue a publish for one vehicle (or all), coalescing a burst."""

        if not self.enabled:
            return
        vins = [vin] if vin else list(self._coordinator.data.keys())
        for target in vins:
            if not target or target in self._timers:
                continue

            @callback
            def _fire(_now: Any, target: str = target) -> None:
                self._timers.pop(target, None)
                self.hass.async_create_task(self.async_publish(target), eager_start=False)

            self._timers[target] = async_call_later(self.hass, PUBLISH_DEBOUNCE_S, _fire)

    @callback
    def async_shutdown(self) -> None:
        """Cancel pending publishes. Retained data deliberately stays put.

        A reload, a Home Assistant restart or an upgrade all pass through here,
        and in every one of them the car's SoC is still the last true thing we
        knew -- wiping the broker on the way out would blank evcc for however
        long the restart takes. Only an explicit "turn the bridge off" clears the
        topics (see :meth:`async_clear_all`).
        """

        self._closed = True
        for cancel in self._timers.values():
            cancel()
        self._timers.clear()

    # --- publishing --------------------------------------------------------

    def snapshot(self, vin: str) -> BridgeSnapshot:
        """What we would tell a charge controller about this car right now."""

        return self._coordinator.bridge_snapshot(vin)

    async def async_publish(self, vin: str, *, force: bool = False) -> None:
        """Publish whatever changed for one vehicle.

        ``force`` re-sends everything, which is what the heartbeat does; without
        it only changed topics go out, so a charging car's SoC is the only thing
        moving on the broker.
        """

        if not self.enabled:
            return
        if not self.available():
            if not self._warned_unavailable:
                self._warned_unavailable = True
                _LOGGER.warning(
                    "The evcc/wallbox bridge is switched on but Home Assistant has "
                    "no MQTT integration set up, so there is nowhere to publish. "
                    "Add the MQTT integration (Settings > Devices & services) and "
                    "reload BavarianData."
                )
            return
        self._warned_unavailable = False

        snapshot = self.snapshot(vin)
        payloads = bridge_payloads(snapshot)
        previous = self._published.get(vin, {})
        if not payloads and not previous:
            # Nothing known and nothing published: leave the broker alone rather
            # than retaining an empty vehicle a charge controller can't use.
            return

        prefix = self._config.prefix
        old_prefix = self._prefixes.get(vin)
        if old_prefix is not None and old_prefix != prefix:
            # The prefix changed under us. Clear the old tree first, or the
            # broker keeps serving a second, frozen copy of this car forever.
            await self._async_clear(vin, old_prefix, tuple(previous))
            previous = {}

        changed = {
            suffix: payload
            for suffix, payload in payloads.items()
            if force or previous.get(suffix) != payload
        }
        # A value we used to know and no longer do must not stay on the broker.
        stale = tuple(suffix for suffix in previous if suffix not in payloads)

        if not changed and not stale:
            return

        for topic, payload in bridge_topics(prefix, vin, changed).items():
            await self._async_send(topic, payload)
        if stale:
            await self._async_clear(vin, prefix, stale)

        # Recorded as published even if a send failed: ``_async_send`` swallows
        # broker errors on purpose (nothing upstream may break because MQTT
        # did), and the five-minute forced heartbeat is what repairs a topic
        # that did not make it. Retrying here would mean a broker outage turning
        # into a publish storm.
        self._published[vin] = payloads
        self._prefixes[vin] = prefix
        if debug_enabled() and changed:
            _LOGGER.debug(
                "[bridge] %s published %s under %s/%s",
                vin,
                sorted(changed),
                prefix,
                vin,
            )

    async def async_publish_all(self, *, force: bool = False) -> None:
        """The heartbeat, and the first publish after setup."""

        if not self.enabled:
            return
        for vin in list(self._coordinator.data.keys()):
            await self.async_publish(vin, force=force)

    async def async_clear_all(self) -> None:
        """Remove every retained topic this bridge ever published.

        Called when the bridge is switched off or its prefix changes. Leaving
        the data behind would let a charge controller go on charging against a
        SoC that stopped updating the moment the user turned the bridge off --
        the failure mode that would make this feature worse than not having it.
        """

        for vin in list(self._published) or list(self._coordinator.data.keys()):
            prefix = self._prefixes.get(vin, self._config.prefix)
            await self._async_clear(vin, prefix, ALL_TOPICS)
        self._published.clear()
        self._prefixes.clear()

    async def _async_clear(self, vin: str, prefix: str, suffixes: tuple[str, ...]) -> None:
        for suffix in suffixes:
            await self._async_send(f"{prefix}/{vin}/{suffix}", _CLEAR, clearing=True)
        for suffix in suffixes:
            self._published.get(vin, {}).pop(suffix, None)

    async def _async_send(self, topic: str, payload: str, *, clearing: bool = False) -> None:
        """One publish, with the broker's problems kept out of the stream.

        Nothing upstream of here may fail because MQTT did: this runs off the
        same signals that drive the entities and the charging ledger, and a
        broker that has gone away must not take the integration with it. A clear
        is always retained regardless of the setting -- a non-retained empty
        message deletes nothing.
        """

        try:
            await mqtt.async_publish(
                self.hass,
                topic,
                payload,
                qos=0,
                retain=True if clearing else self._config.retain,
            )
        except HomeAssistantError as err:
            _LOGGER.debug("[bridge] could not publish %s: %s", topic, err)
        except Exception:  # noqa: BLE001 - a broker must never break the stream
            _LOGGER.exception("[bridge] unexpected error publishing %s", topic)
