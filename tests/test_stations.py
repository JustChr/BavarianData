"""Charging places, worked out from stored sessions (``history/stations.py``)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .conftest import load_module

models = load_module("history.models")
stations = load_module("history.stations")

ChargingSession = models.ChargingSession
charging_places = stations.charging_places

T0 = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)
HOME = {"zone": "Home"}
# Two posts of one charging park, ~30 m apart, and a site 5 km away.
PARK_A = {"zone": None, "lat": 48.30000, "lon": 11.90000, "provider": "Ionity"}
PARK_B = {"zone": None, "lat": 48.30027, "lon": 11.90000, "provider": "Ionity"}
FAR = {"zone": None, "lat": 48.34500, "lon": 11.90000}


def _session(
    day: int,
    location,
    *,
    minutes: float = 30,
    kwh: float = 40.0,
    soc: tuple[float, float] = (20, 60),
    peak: float | None = 150.0,
    **extra,
) -> ChargingSession:
    start = T0 + timedelta(days=day)
    return ChargingSession(
        vin="WBY1",
        start=start,
        end=start + timedelta(minutes=minutes),
        soc_start=soc[0],
        soc_end=soc[1],
        energy_kwh=kwh,
        peak_power_kw=peak,
        location=location,
        **extra,
    )


def _by_id(places):
    return {place["id"]: place for place in places}


# --- grouping ----------------------------------------------------------------


def test_posts_of_one_park_are_one_place_and_a_distant_site_another():
    places = charging_places(
        [_session(0, PARK_A), _session(1, PARK_B), _session(2, FAR)], home_zone="Home"
    )

    assert [place["visits"] for place in places] == [2, 1]
    park = places[0]
    assert park["provider"] == "Ionity"
    assert park["latitude"] == round((48.30000 + 48.30027) / 2, 5)


def test_a_zone_is_a_place_even_when_its_charges_carry_points():
    places = charging_places(
        [_session(0, {"zone": "Home", "lat": 48.1, "lon": 11.5}), _session(1, HOME)],
        home_zone="Home",
    )

    (home,) = places
    assert home["id"] == "zone:Home"
    assert home["visits"] == 2
    assert home["latitude"] == 48.1


def test_an_address_only_charge_joins_the_point_with_that_address():
    address = "Main Street 1, Town"
    places = charging_places(
        [
            _session(0, {"zone": None, "address": address, "lat": 48.3, "lon": 11.9}),
            _session(1, {"zone": None, "address": address}),
        ]
    )

    (place,) = places
    assert place["visits"] == 2
    assert place["address"] == address


def test_address_only_charges_without_a_point_group_by_address():
    places = charging_places(
        [
            _session(0, {"zone": None, "address": "A"}),
            _session(1, {"zone": None, "address": "A"}),
            _session(2, {"zone": None, "address": "B"}),
        ]
    )

    assert sorted((p["address"], p["visits"]) for p in places) == [("A", 2), ("B", 1)]


def test_no_position_at_all_counts_as_home_and_says_so():
    """The tariff's rule: a car that streams no GPS charges at home."""

    (home,) = charging_places([_session(0, None), _session(1, None)], home_zone="Home")
    assert home["zone"] == "Home"
    assert home["assumed"] is True

    (mixed,) = charging_places([_session(0, None), _session(1, HOME)], home_zone="Home")
    assert mixed["assumed"] is False


def test_unnamed_positions_share_one_place_without_performance():
    """Several chargers under one label: their speeds say nothing together."""

    (place,) = charging_places([_session(0, {"zone": None}), _session(1, {"zone": None})])

    assert place["unnamed"] is True
    assert place["visits"] == 2
    assert place["avg_kw"] is None
    assert place["curves"] == []


def test_open_charges_are_left_out():
    running = _session(0, HOME)
    running.end = None
    assert charging_places([running]) == []


def test_most_visited_first_then_most_recent():
    places = charging_places(
        [
            _session(0, {"zone": None, "address": "Old"}),
            _session(5, {"zone": None, "address": "New"}),
            _session(1, HOME),
            _session(2, HOME),
        ],
        home_zone="Home",
    )

    assert [p["address"] or p["zone"] for p in places] == ["Home", "New", "Old"]


# --- performance ---------------------------------------------------------------


def test_one_visit_reports_that_visit_and_two_report_the_median():
    (single,) = charging_places([_session(0, PARK_A, minutes=30, kwh=40)])
    assert single["avg_kw"] == 80.0
    assert single["power_visits"] == 1
    assert single["best_kw"] is None  # a best needs something to beat

    (pair,) = charging_places(
        [_session(0, PARK_A, minutes=30, kwh=40), _session(1, PARK_A, minutes=30, kwh=60)]
    )
    assert pair["avg_kw"] == 100.0
    assert pair["best_kw"] == 120.0
    assert pair["power_visits"] == 2


def test_minutes_per_ten_percent_counts_only_the_steep_part():
    (place,) = charging_places(
        [
            _session(0, PARK_A, minutes=20, soc=(20, 60)),  # 5 min per 10 %
            _session(1, PARK_A, minutes=60, soc=(20, 95)),  # into the taper: left out
            _session(2, PARK_A, minutes=5, soc=(50, 60)),  # too short a span: left out
        ]
    )

    assert place["minutes_per_10"] == 5.0
    assert place["rate_visits"] == 1


def test_floor_records_never_rate_a_charger():
    """A late start or a restart hole leaves the energy a floor, not a speed."""

    (place,) = charging_places(
        [
            _session(0, PARK_A, late_start=True),
            _session(1, PARK_A, interrupted=True),
        ]
    )

    assert place["visits"] == 2
    assert place["avg_kw"] is None
    assert place["minutes_per_10"] is None


def test_ac_and_dc_visits_are_never_averaged_together():
    (place,) = charging_places(
        [
            _session(0, PARK_A, minutes=30, kwh=50, peak=150),
            _session(1, PARK_A, minutes=30, kwh=60, peak=150),
            _session(2, PARK_A, minutes=60, kwh=10, peak=11),
        ]
    )

    assert place["kind"] == "mixed"
    assert place["avg_kw"] == 110.0  # the two DC visits only
    assert place["visits"] == 3


def test_the_cars_own_word_beats_the_peak():
    assert stations.charge_kind(_session(0, HOME, peak=11, charge_method="dc")) == "dc"
    assert stations.charge_kind(_session(0, HOME, peak=150)) == "dc"
    assert stations.charge_kind(_session(0, HOME, peak=22)) == "ac"
    assert stations.charge_kind(_session(0, HOME, peak=None)) is None


def test_streamed_plug_descriptors():
    dc = "vehicle.body.chargingPort.dcStatus"
    method = "vehicle.drivetrain.electricEngine.charging.method"
    assert stations.streamed_charge_method({dc: "CONNECTED"}) == "dc"
    assert stations.streamed_charge_method({dc: "DISCONNECTED", method: "AC_TYPE2PLUG"}) == "ac"
    assert stations.streamed_charge_method({method: "NOCHARGING"}) is None
    assert stations.streamed_charge_method({}) is None


def test_preconditioning_split_needs_both_sides():
    only_on = charging_places([_session(0, PARK_A, preconditioned=True)])[0]
    assert only_on["preconditioning"] is None

    (place,) = charging_places(
        [
            _session(0, PARK_A, minutes=20, kwh=40, preconditioned=True),
            _session(1, PARK_A, minutes=40, kwh=40, preconditioned=False),
            _session(2, PARK_A, minutes=40, kwh=40),
        ]
    )
    split = place["preconditioning"]
    assert split["on"]["visits"] == 1
    assert split["on"]["avg_kw"] == 120.0
    assert split["off"]["visits"] == 1
    assert split["off"]["avg_kw"] == 60.0


def test_curves_are_the_latest_visits_thinned():
    curve = [[i * 10, 100.0] for i in range(500)]
    sessions = [_session(day, PARK_A, power_curve=curve) for day in range(8)]

    (place,) = charging_places(sessions)
    assert len(place["curves"]) == stations.MAX_CURVES
    assert place["curves"][0]["start"] == sessions[-1].start.isoformat()
    assert all(len(c["points"]) <= stations.MAX_CURVE_POINTS for c in place["curves"])


def test_typical_stop_and_energy():
    (place,) = charging_places(
        [
            _session(0, HOME, minutes=60, kwh=10),
            _session(1, HOME, minutes=120, kwh=20, grid_kwh=22),
            _session(2, HOME, minutes=180, kwh=30),
        ],
        home_zone="Home",
    )

    assert place["typical_minutes"] == 120
    assert place["energy_kwh"] == 62.0  # the measured grid figure counts where it exists
    assert place["last_energy_kwh"] == 30
