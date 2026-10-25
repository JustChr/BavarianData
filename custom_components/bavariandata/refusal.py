"""Errors a user reads, named by a translation key.

Home Assistant shows a raised error in the user's language when it carries a
translation key (the ``exceptions`` section of ``translations/``). The HA-free
modules that decide a call cannot go ahead (``service_target.py``,
``service_answers.py``, ``account_rest.py``) raise a :class:`Refusal` with the
key and its placeholders; the HA side turns it into Home Assistant's own error
with :func:`as_ha_error` (in ``services.py``, ``__init__.py``). ``MESSAGES``
holds the English text, which is also what a log line shows;
``tests/test_refusal.py`` holds ``translations/en.json`` to it word for word.
"""

from __future__ import annotations

from typing import Any, Callable

MESSAGES: dict[str, str] = {
    # Which account and car a service call names (service_target.py).
    "device_not_a_car": "Device {device} is not a BavarianData car",
    "vin_device_mismatch": "vin {vin} and device_id ({device_vin}) name different cars",
    "account_not_loaded": "No loaded BavarianData account has entry_id {entry_id}",
    "device_account_not_loaded": "The picked device's BavarianData account is not loaded",
    "vin_not_found": "No BavarianData account has a car with VIN {vin}",
    "no_account": "No BavarianData account is loaded",
    "pick_account": (
        "{count} BavarianData accounts are set up ({accounts}); "
        "pick a car with device_id, or pass entry_id"
    ),
    "vin_not_on_account": "{vin} is not a car of this account",
    "no_car": "This account has no car yet",
    "pick_car": "This account has {count} cars ({cars}); pick one with device_id, or pass vin",
    # A service call's arguments.
    "month_format": "'month' must be 'YYYY-MM', got {value}",
    "month_number": "'month' has no month {month}: {value}",
    "bad_date": "Could not read {key} as a date or date/time: {value}",
    "no_attributes": (
        "No attributes to activate; pass 'attributes' or 'sections', "
        "or choose streamed-data clusters in the options."
    ),
    # What the account can't do right now.
    "no_trip_history": "Trip history is not available",
    "trip_not_found": "No trip {trip} found for {vin}",
    "trips_not_from_reports": (
        "mark_trip_start is used only when trips are detected from the "
        "car's reports (Configure -> Trips -> Trip detection)"
    ),
    "statistics_off": (
        "Statistics import is turned off for this vehicle (Configure -> Charging costs & history)."
    ),
    "no_recorder": (
        "Home Assistant's recorder is not set up, so there are no "
        "long-term statistics to import into."
    ),
    # Asking BMW.
    "no_token": "{service}: no valid BMW token; reauthorize",
    "request_blocked": "{service}: {error}",
    "bmw_error": "{service}: {error}",
    "fetch_incomplete": "{service}: {error}",
    "portal_session_rejected": (
        "BMW rejected the portal session (expired or invalid). "
        "Capture a fresh session from the stream-setup page and retry."
    ),
    "portal_not_found": "BMW returned 404 -- check the mapped_vehicle_id and locale.",
    "portal_failed": "{error}",
    # Setting the account up.
    "missing_credentials": "Missing GCID or ID token",
    "token_refresh_failed": "Initial token refresh failed: {error}",
    "stream_failed": "Unable to connect to BMW MQTT: {error}",
    "stream_failed_after_refresh": "Unable to connect to BMW MQTT after token refresh: {error}",
    # The conversation agent's tools.
    "unknown_car": "No car called {car}; the cars are {cars}",
}


class Refusal(Exception):
    """A call or a setup that cannot go ahead, and the key that says why."""

    def __init__(self, key: str, /, **placeholders: Any) -> None:
        self.key = key
        self.placeholders = {name: str(value) for name, value in placeholders.items()}
        super().__init__(MESSAGES[key].format(**self.placeholders))


def as_ha_error[E: Exception](err: Refusal, kind: Callable[..., E], domain: str) -> E:
    """``err`` as Home Assistant's ``kind`` (a ``HomeAssistantError``), translated."""

    return kind(
        translation_domain=domain,
        translation_key=err.key,
        translation_placeholders=err.placeholders,
    )
