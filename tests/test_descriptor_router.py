"""Each reading goes to the entities that show it, and to no one else."""

from __future__ import annotations

import logging

from .conftest import load_module

router_mod = load_module("descriptor_router")

A, B = "VIN_A", "VIN_B"
SOC = "vehicle.drivetrain.batteryManagement.header"
LAT = "vehicle.cabin.infotainment.navigation.currentLocation.latitude"
LON = "vehicle.cabin.infotainment.navigation.currentLocation.longitude"


def _recorder(log, name):
    return lambda vin, descriptor: log.append((name, vin, descriptor))


def test_a_reading_reaches_its_own_car_and_descriptor_only():
    router, log = router_mod.DescriptorRouter(), []
    router.subscribe(A, (SOC,), _recorder(log, "a-soc"))
    router.subscribe(B, (SOC,), _recorder(log, "b-soc"))
    router.subscribe(A, (LAT, LON), _recorder(log, "a-fix"))

    router.dispatch(A, SOC)
    router.dispatch(A, LON)
    router.dispatch(B, LAT)

    assert log == [("a-soc", A, SOC), ("a-fix", A, LON)]


def test_every_car_and_every_descriptor_listeners():
    router, log = router_mod.DescriptorRouter(), []
    router.subscribe(None, (LAT,), _recorder(log, "any-car-fix"))
    router.subscribe(A, None, _recorder(log, "all-of-a"))
    router.subscribe(None, None, _recorder(log, "all"))
    router.subscribe(A, (LAT,), _recorder(log, "a-lat"))

    router.dispatch(A, LAT)
    router.dispatch(B, SOC)

    # The exact listeners first, then the car's, then any car's, then all.
    assert log == [
        ("a-lat", A, LAT),
        ("all-of-a", A, LAT),
        ("any-car-fix", A, LAT),
        ("all", A, LAT),
        ("all", B, SOC),
    ]


def test_unsubscribe_ends_it_and_leaves_no_key_behind():
    router, log = router_mod.DescriptorRouter(), []
    stop = router.subscribe(A, (LAT, LON, LAT), _recorder(log, "fix"))
    keep = router.subscribe(A, (LAT,), _recorder(log, "other"))
    # A repeated descriptor is one listener, not two calls per reading.
    assert len(router) == 3
    router.dispatch(A, LAT)
    assert log == [("fix", A, LAT), ("other", A, LAT)]

    stop()
    stop()  # twice is harmless, as HA's own unsubscribes are
    router.dispatch(A, LAT)
    router.dispatch(A, LON)
    assert log[2:] == [("other", A, LAT)]
    keep()
    assert len(router) == 0
    assert router._targets == {}


def test_a_listener_may_leave_while_the_reading_is_handed_out():
    router, log = router_mod.DescriptorRouter(), []
    stops = []

    def leaving(vin, descriptor):
        log.append("leaving")
        stops[0]()

    stops.append(router.subscribe(A, (SOC,), leaving))
    router.subscribe(A, (SOC,), _recorder(log, "stays"))

    router.dispatch(A, SOC)
    router.dispatch(A, SOC)

    assert log == ["leaving", ("stays", A, SOC), ("stays", A, SOC)]


def test_one_failing_listener_does_not_silence_the_others(caplog):
    router, log = router_mod.DescriptorRouter(), []

    def broken(vin, descriptor):
        raise RuntimeError("boom")

    router.subscribe(A, (SOC,), broken)
    router.subscribe(A, (SOC,), _recorder(log, "after"))

    with caplog.at_level(logging.ERROR):
        router.dispatch(A, SOC)

    assert log == [("after", A, SOC)]
    assert "Error handing" in caplog.text
    # The car is not named in the log: debug logging is the only place for VINs.
    assert A not in caplog.text
