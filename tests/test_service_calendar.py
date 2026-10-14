"""The service calendar: BMW's service items as all-day events, and the pace behind them.

``conditionBasedServices`` gives each item a due month and sometimes a distance
left; the calendar puts a month item on the 1st and projects a distance-only
item from the car's own pace -- the daily odometer log -- or leaves it out.
"""

from __future__ import annotations

from datetime import date

import pytest

from .conftest import load_module
from .harness import CoordinatorHarness

service_calendar = load_module("service_calendar")
odometer = load_module("history.odometer")

VIN = "WBA00000000000001"
TODAY = date(2026, 10, 14)

# The i5's list as the daily container delivers it (2026-10-10), JSON text.
I5_WIRE = (
    '[{"date":"2027-10","description":"Next change due at the latest by the stated date.",'
    '"id":3,"messageType":"CBS","status":"OK","title":"Brake fluid","text":"-",'
    '"unitOfLengthRemaining":"-"},{"date":"2027-10","description":"-","id":100,'
    '"messageType":"CBS","status":"OK","title":"Vehicle check","text":"-",'
    '"unitOfLengthRemaining":"-"},{"date":"2028-10","description":"Next statutory vehicle '
    'inspection due by the stated date.","id":32,"messageType":"CBS","status":"OK",'
    '"title":"Statutory vehicle inspection","text":"-","unitOfLengthRemaining":"-"},'
    '{"date":"2029-10","description":"Next change due at the latest by the stated date.",'
    '"id":24,"messageType":"CBS","status":"OK","title":"Gas pressure springs for bonnet",'
    '"text":"-","unitOfLengthRemaining":"-"}]'
)


def _events(items, **kwargs):
    return service_calendar.service_events(items, vin=VIN, today=TODAY, **kwargs)


def test_a_due_month_is_an_all_day_event_on_the_first():
    events = _events(I5_WIRE)
    assert [(e.summary, e.start) for e in events] == [
        ("Brake fluid", date(2027, 10, 1)),
        ("Vehicle check", date(2027, 10, 1)),
        ("Statutory vehicle inspection", date(2028, 10, 1)),
        ("Gas pressure springs for bonnet", date(2029, 10, 1)),
    ]
    brake = events[0]
    assert brake.end == date(2027, 10, 2)
    assert brake.uid == f"{VIN}-cbs-3"
    assert brake.description == (
        "Due by the end of October 2027.\nNext change due at the latest by the stated date."
    )
    assert not brake.estimated


def test_bmw_placeholder_description_is_left_out():
    check = [e for e in _events(I5_WIRE) if e.summary == "Vehicle check"][0]
    assert check.description == "Due by the end of October 2027."


def test_a_moved_month_keeps_the_same_event():
    moved = I5_WIRE.replace(
        '"date":"2027-10","description":"Next', '"date":"2027-12","description":"Next', 1
    )
    before = {e.uid: e.start for e in _events(I5_WIRE)}
    after = {e.uid: e.start for e in _events(moved)}
    assert before.keys() == after.keys()
    assert after[f"{VIN}-cbs-3"] == date(2027, 12, 1)


OIL = {
    "date": None,
    "id": 1,
    "status": "OK",
    "title": "Engine oil",
    "unitOfLengthRemaining": "9000",
}


def test_a_distance_only_item_is_projected_from_the_pace_and_says_so():
    (event,) = _events([OIL], km_per_day=50.0)
    assert event.start == date(2027, 4, 12)  # 180 days on
    assert event.estimated
    assert event.description == ("Estimated from your driving: 9,000 km left at about 50 km a day.")


def test_no_pace_means_no_event_rather_than_a_guess():
    assert _events([OIL]) == []
    assert _events([OIL], km_per_day=0.0) == []


def test_an_item_with_date_and_distance_keeps_bmws_date():
    item = {**OIL, "date": "2027-03"}
    (event,) = _events([item], km_per_day=50.0)
    assert event.start == date(2027, 3, 1)
    assert not event.estimated
    assert "Or in 9,000 km, whichever comes first." in event.description


def test_miles_and_language():
    (event,) = _events([OIL], km_per_day=50.0, language="de", miles=True)
    assert event.description == (
        "Geschätzt aus deiner Fahrleistung: noch 5.592 mi bei etwa 31 mi am Tag."
    )
    (event,) = _events(I5_WIRE, language="pl")[:1]
    assert event.description.startswith("Termin do końca października 2027.")
    (event,) = _events(I5_WIRE, language="es")[:1]
    assert event.description.startswith("Vence a finales de octubre de 2027.")


def test_status_marks_the_title():
    items = [
        {**OIL, "date": "2026-09", "status": "OVERDUE"},
        {**OIL, "id": 2, "date": "2026-11", "status": "PENDING"},
    ]
    assert [e.summary for e in _events(items)] == ["Engine oil (overdue)", "Engine oil (due soon)"]


@pytest.mark.parametrize(
    "junk",
    [None, "", "not json", 7, {"a": 1}, [None, 3, {"title": "-"}, {"title": "X", "date": "soon"}]],
)
def test_junk_gives_no_events(junk):
    assert _events(junk) == []


def test_next_event_is_today_or_the_soonest_after():
    events = _events([{**OIL, "date": "2026-10"}, {**OIL, "id": 2, "date": "2026-11"}])
    # October's event was on the 1st and is over; November's is next.
    assert service_calendar.next_event(events, TODAY).start == date(2026, 11, 1)
    assert service_calendar.next_event(events, date(2026, 10, 1)).start == date(2026, 10, 1)
    assert service_calendar.next_event([], TODAY) is None


def test_every_language_has_englishs_keys_and_placeholders():
    import string

    def fields(text):
        return {name for _, name, _, _ in string.Formatter().parse(text) if name}

    en = service_calendar.STRINGS["en"]
    for lang, table in service_calendar.STRINGS.items():
        assert table.keys() == en.keys(), lang
        for key, text in table.items():
            assert fields(text) == fields(en[key]), (lang, key)
        assert len(service_calendar.MONTHS[lang]) == 12, lang
    assert service_calendar.MONTHS.keys() == service_calendar.STRINGS.keys()


# --- the odometer log ------------------------------------------------------


def test_the_log_keeps_the_days_highest_reading_and_ignores_replays():
    log: dict = {}
    assert odometer.note_reading(log, date(2026, 9, 1), 1000)
    assert odometer.note_reading(log, date(2026, 9, 1), 1040)
    assert not odometer.note_reading(log, date(2026, 9, 1), 1020)
    # A REST catch-up replaying an older reading on a later day folds nothing.
    assert not odometer.note_reading(log, date(2026, 9, 2), 990)
    assert log == {"2026-09-01": 1040.0}


def test_a_pace_needs_four_weeks_of_span():
    log: dict = {}
    odometer.note_reading(log, date(2026, 9, 20), 1000)
    odometer.note_reading(log, date(2026, 10, 14), 2200)  # 24 days
    assert odometer.km_per_day(log, TODAY) is None
    odometer.note_reading(log, date(2026, 9, 14), 700)  # 30 days
    assert odometer.km_per_day(log, TODAY) == pytest.approx(50.0)


def test_the_pace_reads_only_the_last_ninety_days():
    log: dict = {}
    odometer.note_reading(log, date(2025, 1, 1), 0)  # a long-gone road trip era
    odometer.note_reading(log, date(2026, 7, 16), 20000)
    odometer.note_reading(log, date(2026, 10, 14), 21800)
    assert odometer.km_per_day(log, TODAY) == pytest.approx(20.0)


# --- through the coordinator ------------------------------------------------


@pytest.fixture
def h():
    harness = CoordinatorHarness()
    yield harness
    harness.close()


def test_streamed_odometer_feeds_the_log_and_survives_a_restart(h):
    h.send(0, odometer=12000, step="morning")
    h.send(60, odometer=12035, step="drove")
    day = h.at(60).date().isoformat()
    assert h.coordinator.history.odometer_log(h.vin) == {day: 12035.0}
    h.restart()
    assert h.coordinator.history.odometer_log(h.vin) == {day: 12035.0}
    assert h.coordinator.km_per_day(h.vin) is None  # one day is no pace
