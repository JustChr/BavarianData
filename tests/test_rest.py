"""The one way into BMW's REST API: a valid token, a quota slot, then the request.

Every renewal hands the stream a new ID token, and the stream reconnects for it
(about ten seconds deaf, since BMW refuses a quicker reconnect). Renewing before
every request meant a manual fetch, or a restart's catch-up of two cars, cost
the stream one drop per request. The keeper renews only when the token is about
to lapse, and one renewal at a time.
"""

from __future__ import annotations

import asyncio

import pytest

from .conftest import FakeResponse, FakeSession, load_module
from .fake_account import gateway

rest = load_module("rest")
container = load_module("container")
device_flow = load_module("device_flow")

HOUR = 3600


class _Account:
    """The stored token fields, and a renewal that writes new ones."""

    def __init__(self, *, received_at=0.0, expires_in=HOUR, token="t0", fail=None) -> None:
        self.data = {"access_token": token, "received_at": received_at, "expires_in": expires_in}
        self.renewals = 0
        self.fail = fail
        self.now = 0.0

    async def renew(self) -> None:
        self.renewals += 1
        await asyncio.sleep(0)  # let a concurrent request queue up behind it
        if self.fail is not None:
            raise self.fail
        self.data = {
            "access_token": f"t{self.renewals}",
            "received_at": self.now,
            "expires_in": HOUR,
        }

    def keeper(self) -> object:
        return rest.TokenKeeper(self.renew, lambda: self.data, clock=lambda: self.now)


def test_a_token_with_time_left_is_used_without_renewing():
    account = _Account()
    account.now = 30 * 60
    assert asyncio.run(account.keeper().access_token()) == "t0"
    assert account.renewals == 0


def test_a_token_about_to_lapse_is_renewed_first():
    account = _Account()
    account.now = HOUR - 60
    assert asyncio.run(account.keeper().access_token()) == "t1"
    assert account.renewals == 1


@pytest.mark.parametrize(
    "fields",
    [
        {"received_at": None},
        {"expires_in": None},
        {"expires_in": "soon"},
        {"access_token": ""},
    ],
)
def test_a_token_of_unknown_age_is_renewed_rather_than_risked(fields):
    account = _Account()
    account.data.update(fields)
    assert asyncio.run(account.keeper().access_token()) == "t1"


def test_requests_waiting_on_one_renewal_share_it():
    account = _Account()
    account.now = 2 * HOUR
    keeper = account.keeper()

    async def five_at_once():
        return await asyncio.gather(*(keeper.access_token() for _ in range(5)))

    assert asyncio.run(five_at_once()) == ["t1"] * 5
    assert account.renewals == 1


def test_renew_always_renews():
    account = _Account()
    asyncio.run(account.keeper().renew())
    assert account.renewals == 1


def test_a_failed_renewal_reaches_the_caller():
    account = _Account(fail=device_flow.CardataAuthError("invalid_grant", status=400))
    account.now = 2 * HOUR
    with pytest.raises(device_flow.CardataAuthError):
        asyncio.run(account.keeper().access_token())


def test_a_renewal_that_leaves_no_token_is_an_auth_error():
    account = _Account()
    account.now = 2 * HOUR

    async def renew_to_nothing() -> None:
        account.data = {}

    keeper = rest.TokenKeeper(renew_to_nothing, lambda: account.data, clock=lambda: account.now)
    with pytest.raises(device_flow.CardataAuthError):
        asyncio.run(keeper.access_token())


class _Quota:
    def __init__(self, left: int) -> None:
        self.left = left
        self.claims = 0

    async def claim(self) -> None:
        if self.left <= 0:
            raise rest.CardataQuotaError("spent")
        self.left -= 1
        self.claims += 1


def test_the_gateway_sends_the_request_with_a_token_and_a_slot():
    account, quota, sent = _Account(), _Quota(1), []

    async def request(session, token, vin):
        sent.append((session, token, vin))
        return {"ok": vin}

    gateway = rest.RestGateway("session", account.keeper(), quota.claim)
    assert asyncio.run(gateway.call(request, "VIN1")) == {"ok": "VIN1"}
    assert sent == [("session", "t0", "VIN1")]
    assert quota.claims == 1


def test_no_token_spends_no_quota():
    account = _Account(fail=device_flow.CardataAuthError("invalid_grant", status=400))
    account.now = 2 * HOUR
    quota = _Quota(5)

    async def request(session, token):
        raise AssertionError("sent without a token")

    gateway = rest.RestGateway(None, account.keeper(), quota.claim)
    with pytest.raises(device_flow.CardataAuthError):
        asyncio.run(gateway.call(request))
    assert quota.claims == 0


def test_a_spent_quota_sends_nothing():
    async def request(session, token):
        raise AssertionError("sent past the quota")

    gateway = rest.RestGateway(None, _Account().keeper(), _Quota(0).claim)
    with pytest.raises(rest.CardataQuotaError):
        asyncio.run(gateway.call(request))


def test_container_requests_count_against_the_quota():
    # BMW's limit covers "the use of the CarData APIs": a reset (list, create,
    # delete) is three requests, and they used to go uncounted.
    quota = _Quota(5)
    session = FakeSession(
        [
            FakeResponse(
                200, [{"containerId": "old", "purpose": container.HV_BATTERY_CONTAINER_PURPOSE}]
            ),
            FakeResponse(201, {"containerId": "new"}),
            FakeResponse(204, None),
        ]
    )
    manager = container.CardataContainerManager(
        rest=gateway(session, claim=quota.claim), entry_id="e", initial_container_id="old"
    )
    assert asyncio.run(manager.async_reset_hv_container()) == "new"
    assert quota.claims == 3


def test_a_spent_quota_stops_a_container_request_before_it_is_sent():
    session = FakeSession([])
    manager = container.CardataContainerManager(
        rest=gateway(session, claim=_Quota(0).claim), entry_id="e"
    )
    with pytest.raises(container.CardataContainerError):
        asyncio.run(manager.async_ensure_hv_container())
    assert session.calls == []


def test_bmws_error_code_reaches_the_container_error():
    session = FakeSession(
        [
            FakeResponse(200, {"containers": []}),
            FakeResponse(
                403, {"exveErrorId": "CU-124", "exveErrorMsg": "Maximum number of containers"}
            ),
        ]
    )
    manager = container.CardataContainerManager(rest=gateway(session), entry_id="e")
    with pytest.raises(container.CardataContainerError) as err:
        asyncio.run(manager.async_ensure_hv_container())
    assert err.value.error_id == "CU-124"
    assert "Maximum number of containers" in str(err.value)


# What BMW answered, for the diagnostics (#296): the stream ran for days while
# every fetch was refused with CU-403, and the file could not show it.


def test_outcomes_count_successes_and_name_the_last_refusal():
    now = [1_700_000_000.0]
    gw = rest.RestGateway(None, _Account().keeper(), clock=lambda: now[0])
    api = load_module("api")

    async def get_vehicle_mappings(session, token):
        return []

    async def async_get_basic_data(session, token, vin):
        raise api.CardataApiError(
            f"HTTP 403 (CU-403: Bad Request. Forbidden) for GET /x/{vin}",
            status=403,
            error_id="CU-403",
        )

    asyncio.run(gw.call(get_vehicle_mappings))
    now[0] += 60
    with pytest.raises(api.CardataApiError):
        asyncio.run(gw.call(async_get_basic_data, "WBA00000000000001"))

    report = gw.outcomes.as_dict()
    assert report == {
        "succeeded": 1,
        "failed": 1,
        "last_success_at": "2023-11-14T22:13:20+00:00",
        "last_failure": {
            "at": "2023-11-14T22:14:20+00:00",
            "request": "get_basic_data",
            "status": 403,
            "error_id": "CU-403",
        },
    }
    # The request's name, never its path or BMW's words: the path holds the VIN.
    assert "WBA00000000000001" not in repr(report)


def test_an_unreachable_bmw_is_a_failure_without_a_status():
    gw = rest.RestGateway(None, _Account().keeper())
    api = load_module("api")

    async def request(session, token):
        raise api.CardataApiError("Timed out for GET /customers/vehicles/mappings")

    with pytest.raises(api.CardataApiError):
        asyncio.run(gw.call(request, what="mappings"))
    failure = gw.outcomes.as_dict()["last_failure"]
    assert (failure["request"], failure["status"], failure["error_id"]) == ("mappings", None, None)


def test_a_request_never_sent_is_no_outcome():
    # No token, or no quota: BMW was not asked, so it neither worked nor failed.
    gw = rest.RestGateway(None, _Account().keeper(), _Quota(0).claim)

    async def request(session, token):
        raise AssertionError("sent past the quota")

    with pytest.raises(rest.CardataQuotaError):
        asyncio.run(gw.call(request))
    assert gw.outcomes.as_dict() == {
        "succeeded": 0,
        "failed": 0,
        "last_success_at": None,
        "last_failure": None,
    }


def test_a_container_refusal_is_named_by_method_not_path():
    session = FakeSession(
        [FakeResponse(200, {"containers": []}), FakeResponse(403, {"exveErrorId": "CU-403"})]
    )
    gw = gateway(session)
    manager = container.CardataContainerManager(rest=gw, entry_id="e")
    with pytest.raises(container.CardataContainerError):
        asyncio.run(manager.async_ensure_hv_container())
    failure = gw.outcomes.as_dict()["last_failure"]
    assert failure["error_id"] == "CU-403"
    assert failure["request"].endswith(" containers")
