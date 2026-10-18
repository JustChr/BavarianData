"""Which account and which car a service call means.

A call names its target in up to four ways -- an ``entry_id``, a ``device_id``
(a car, or an account's debug device), a ``vin``, or nothing at all -- and with
one car in one account every one of them leads to the same place. With two
cars, "nothing at all" used to mean whichever car had streamed first since the
restart, and with two accounts a call that could not be placed was a log line
while the automation reported success. The rules here refuse instead, and the
refusal names what there is to choose from.

No Home Assistant imports: the registry lookups stay in ``services.py``, the
decisions are unit-tested directly (``tests/test_service_target.py``).
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from .debug import mask_vin

__all__ = ["TargetError", "choose_account", "choose_vin", "requested_vin"]


class TargetError(ValueError):
    """The call cannot be placed; the message says what to pass instead."""


def requested_vin(vin: Any, device_vin: Optional[str]) -> Optional[str]:
    """The car a call names, from its ``vin`` and its ``device_id``.

    Either is enough; both are fine when they agree. ``device_vin`` is the VIN
    of the picked device, ``None`` for no device or the debug device (which
    names an account, not a car).
    """

    text = vin.strip() if isinstance(vin, str) else None
    if text and device_vin and text != device_vin:
        raise TargetError(
            f"vin {mask_vin(text)} and device_id ({mask_vin(device_vin)}) name different cars"
        )
    return text or device_vin or None


def choose_account(
    titles: Mapping[str, str],
    *,
    entry_id: Optional[str] = None,
    device_entry: Optional[str] = None,
    vin: Optional[str] = None,
    vin_entry: Optional[str] = None,
) -> str:
    """The id of the loaded account a call acts on.

    ``titles`` maps every loaded entry id to its title. An explicit
    ``entry_id`` wins, then the account of the picked device, then the account
    whose device carries ``vin``; with nothing named, only a lone account is a
    safe guess.
    """

    if entry_id:
        if entry_id not in titles:
            raise TargetError(f"No loaded BavarianData account has entry_id {entry_id}")
        return entry_id
    if device_entry:
        if device_entry not in titles:
            raise TargetError("The picked device's BavarianData account is not loaded")
        return device_entry
    if vin and vin_entry in titles:
        return vin_entry
    if vin and len(titles) > 1:
        raise TargetError(f"No BavarianData account has a car with VIN {mask_vin(vin)}")
    if len(titles) == 1:
        return next(iter(titles))
    if not titles:
        raise TargetError("No BavarianData account is loaded")
    listed = ", ".join(sorted(title or entry for entry, title in titles.items()))
    raise TargetError(
        f"{len(titles)} BavarianData accounts are set up ({listed}); "
        "pick a car with device_id, or pass entry_id"
    )


def choose_vin(
    requested: Optional[str],
    account_vins: Sequence[str],
    names: Optional[Mapping[str, str]] = None,
    *,
    required: bool = True,
) -> Optional[str]:
    """The car of the account a call acts on.

    A named car has to be one of the account's: the VIN goes into request
    paths and store keys, and a typo would spend quota on a car that isn't
    there. With nothing named, a lone car is the answer; with several, a
    service that acts on one car refuses and lists them, while one that reads
    every car (``required=False``) gets ``None`` and reads them all.
    """

    if requested:
        if requested not in account_vins:
            raise TargetError(f"{mask_vin(requested)} is not a car of this account")
        return requested
    if not required:
        return None
    if len(account_vins) == 1:
        return account_vins[0]
    if not account_vins:
        raise TargetError("This account has no car yet")
    names = names or {}
    listed = ", ".join(names.get(vin) or mask_vin(vin) for vin in account_vins)
    raise TargetError(
        f"This account has {len(account_vins)} cars ({listed}); "
        "pick one with device_id, or pass vin"
    )
