"""The car's service items as calendar events.

``vehicle.status.conditionBasedServices`` (``structured_values.py``) is the list
of service items the car tracks -- brake fluid, vehicle check, statutory
inspection -- each with a due **month** (``"2027-10"``) and sometimes a distance
left. The daily container already delivers it, so a calendar costs no quota.

Rules, from ``docs/roadmap.md`` (*Service calendar*):

* BMW gives a month, not a day: an item is an **all-day event on the 1st**,
  described as due by the end of that month. When BMW's month moves, the event
  moves with it -- the uid is the item's id, so it is the same event.
* An item with a distance but no date is projected from the car's own pace
  (``history/odometer.py``) and says so. Without a pace there is no event: a
  wrong date is worse than none.
* An item with both keeps BMW's date and mentions the distance.

The texts are baked in per language, like the month report's
(``history/export.py``): a calendar event is not an entity string, so Home
Assistant's translation files cannot carry it. ``tools/i18n_gaps.py`` and
``tests/test_service_calendar.py`` hold every language to English's keys.

Home Assistant-free on purpose; ``calendar.py`` turns these into events.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Iterable, Optional

from .structured_values import structured_state

CBS = "vehicle.status.conditionBasedServices"

KM_PER_MILE = 1.609344

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "due_by": "Due by the end of {month}.",
        "distance_too": "Or in {distance}, whichever comes first.",
        "estimate": "Estimated from your driving: {distance} left at about {pace} a day.",
        "due_soon": "{title} (due soon)",
        "overdue": "{title} (overdue)",
    },
    "de": {
        "due_by": "Fällig bis Ende {month}.",
        "distance_too": "Oder in {distance}, je nachdem, was zuerst eintritt.",
        "estimate": "Geschätzt aus deiner Fahrleistung: noch {distance} bei etwa {pace} am Tag.",
        "due_soon": "{title} (bald fällig)",
        "overdue": "{title} (überfällig)",
    },
    "fr": {
        "due_by": "À effectuer avant la fin {month}.",
        "distance_too": "Ou dans {distance}, selon ce qui arrive en premier.",
        "estimate": "Estimé d'après votre conduite : encore {distance} à environ {pace} par jour.",
        "due_soon": "{title} (bientôt)",
        "overdue": "{title} (en retard)",
    },
    "it": {
        "due_by": "Da eseguire entro la fine di {month}.",
        "distance_too": "Oppure tra {distance}, a seconda di quale arriva prima.",
        "estimate": "Stimato dalla tua guida: ancora {distance} a circa {pace} al giorno.",
        "due_soon": "{title} (in scadenza)",
        "overdue": "{title} (scaduto)",
    },
    "es": {
        "due_by": "Vence a finales de {month}.",
        "distance_too": "O dentro de {distance}, lo que ocurra primero.",
        "estimate": "Estimado según tu conducción: quedan {distance} a unos {pace} al día.",
        "due_soon": "{title} (vence pronto)",
        "overdue": "{title} (vencido)",
    },
    "nl": {
        "due_by": "Uiterlijk eind {month}.",
        "distance_too": "Of over {distance}, wat het eerst komt.",
        "estimate": "Geschat op basis van je rijgedrag: nog {distance} bij ongeveer {pace} per dag.",
        "due_soon": "{title} (binnenkort)",
        "overdue": "{title} (te laat)",
    },
    "pl": {
        "due_by": "Termin do końca {month}.",
        "distance_too": "Lub za {distance}, w zależności od tego, co nastąpi wcześniej.",
        "estimate": "Szacunek na podstawie Twojej jazdy: zostało {distance} przy ok. {pace} dziennie.",
        "due_soon": "{title} (wkrótce)",
        "overdue": "{title} (po terminie)",
    },
    "pt": {
        "due_by": "Prazo até ao fim de {month}.",
        "distance_too": "Ou dentro de {distance}, o que acontecer primeiro.",
        "estimate": "Estimado pela sua condução: faltam {distance} a cerca de {pace} por dia.",
        "due_soon": "{title} (em breve)",
        "overdue": "{title} (em atraso)",
    },
    "cs": {
        "due_by": "Termín do konce {month}.",
        "distance_too": "Nebo za {distance}, podle toho, co nastane dřív.",
        "estimate": "Odhad podle vaší jízdy: zbývá {distance} při zhruba {pace} denně.",
        "due_soon": "{title} (brzy)",
        "overdue": "{title} (po termínu)",
    },
    "sv": {
        "due_by": "Senast i slutet av {month}.",
        "distance_too": "Eller om {distance}, beroende på vad som kommer först.",
        "estimate": "Uppskattat från din körning: {distance} kvar med cirka {pace} per dag.",
        "due_soon": "{title} (snart)",
        "overdue": "{title} (försenad)",
    },
}

# The month as it reads inside ``due_by`` -- the grammatical case that phrase
# needs (genitive in Polish and Czech, "de" before the year in French, Spanish
# and Portuguese), so each language's sentence stays whole.
MONTHS: dict[str, tuple[str, ...]] = {
    "en": (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ),
    "de": (
        "Januar",
        "Februar",
        "März",
        "April",
        "Mai",
        "Juni",
        "Juli",
        "August",
        "September",
        "Oktober",
        "November",
        "Dezember",
    ),
    "fr": (
        "janvier",
        "février",
        "mars",
        "avril",
        "mai",
        "juin",
        "juillet",
        "août",
        "septembre",
        "octobre",
        "novembre",
        "décembre",
    ),
    "it": (
        "gennaio",
        "febbraio",
        "marzo",
        "aprile",
        "maggio",
        "giugno",
        "luglio",
        "agosto",
        "settembre",
        "ottobre",
        "novembre",
        "dicembre",
    ),
    "es": (
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "octubre",
        "noviembre",
        "diciembre",
    ),
    "nl": (
        "januari",
        "februari",
        "maart",
        "april",
        "mei",
        "juni",
        "juli",
        "augustus",
        "september",
        "oktober",
        "november",
        "december",
    ),
    "pl": (
        "stycznia",
        "lutego",
        "marca",
        "kwietnia",
        "maja",
        "czerwca",
        "lipca",
        "sierpnia",
        "września",
        "października",
        "listopada",
        "grudnia",
    ),
    "pt": (
        "janeiro",
        "fevereiro",
        "março",
        "abril",
        "maio",
        "junho",
        "julho",
        "agosto",
        "setembro",
        "outubro",
        "novembro",
        "dezembro",
    ),
    "cs": (
        "ledna",
        "února",
        "března",
        "dubna",
        "května",
        "června",
        "července",
        "srpna",
        "září",
        "října",
        "listopadu",
        "prosince",
    ),
    "sv": (
        "januari",
        "februari",
        "mars",
        "april",
        "maj",
        "juni",
        "juli",
        "augusti",
        "september",
        "oktober",
        "november",
        "december",
    ),
}

# "{month} {year}" unless a language joins them differently. French needs the
# article's partner ("fin octobre 2027" reads "la fin d'octobre" in full, but the
# short form is the usual one on a due date).
_MONTH_YEAR: dict[str, str] = {
    "es": "{month} de {year}",
    "pt": "{month} de {year}",
}

# Thousands separator per language (a narrow no-break space where it is a space).
_THOUSANDS: dict[str, str] = {
    "en": ",",
    "de": ".",
    "it": ".",
    "es": ".",
    "nl": ".",
    "pt": ".",
    "fr": " ",
    "pl": " ",
    "cs": " ",
    "sv": " ",
}


def _lang(language: Optional[str]) -> str:
    code = (language or "en")[:2].lower()
    return code if code in STRINGS else "en"


@dataclass(frozen=True)
class ServiceEvent:
    """One all-day event: ``start`` inclusive, ``end`` exclusive (the next day)."""

    uid: str
    summary: str
    start: date
    description: str
    estimated: bool = False

    @property
    def end(self) -> date:
        return self.start + timedelta(days=1)


def _due_month(value: Any) -> Optional[date]:
    """The 1st of BMW's due month (``"2027-10"``, or a full date), else ``None``."""

    if not isinstance(value, str):
        return None
    try:
        year, month = (int(part) for part in value.strip()[:7].split("-"))
        return date(year, month, 1)
    except TypeError, ValueError:
        return None


def _distance_km(value: Any) -> Optional[float]:
    """The distance left in km, from BMW's ``unitOfLengthRemaining`` (``"-"`` = none)."""

    if isinstance(value, bool):
        return None
    try:
        km = float(value)
    except TypeError, ValueError:
        return None
    return km if km >= 0 else None


def _text(value: Any) -> Optional[str]:
    """BMW's text, or ``None`` for its ``"-"`` / empty placeholders."""

    if not isinstance(value, str):
        return None
    text = value.strip()
    return text if text and text != "-" else None


def format_distance(km: float, language: Optional[str], *, miles: bool = False) -> str:
    """``12,000 km`` (or miles), grouped the way the language groups thousands."""

    value = km / KM_PER_MILE if miles else km
    grouped = f"{round(value):,}".replace(",", _THOUSANDS.get(_lang(language), ","))
    return f"{grouped} {'mi' if miles else 'km'}"


def format_month(day: date, language: Optional[str]) -> str:
    lang = _lang(language)
    pattern = _MONTH_YEAR.get(lang, "{month} {year}")
    return pattern.format(month=MONTHS[lang][day.month - 1], year=day.year)


def service_events(
    items: Iterable[Any],
    *,
    vin: str,
    today: date,
    km_per_day: Optional[float] = None,
    language: Optional[str] = None,
    miles: bool = False,
) -> list[ServiceEvent]:
    """Turn BMW's service items into events, soonest first.

    ``items`` is the decoded list (or the JSON text BMW sends). Items with
    neither a due month nor a distance that can be projected are left out.
    """

    _count, decoded = structured_state(items)
    if not isinstance(decoded, list):
        return []
    strings = STRINGS[_lang(language)]
    events: list[ServiceEvent] = []
    for item in decoded:
        if not isinstance(item, dict):
            continue
        title = _text(item.get("title")) or _text(item.get("text"))
        if title is None:
            continue
        month = _due_month(item.get("date"))
        distance = _distance_km(item.get("unitOfLengthRemaining"))
        lines: list[str] = []
        estimated = False
        if month is not None:
            start = month
            lines.append(strings["due_by"].format(month=format_month(month, language)))
            if distance is not None:
                lines.append(
                    strings["distance_too"].format(
                        distance=format_distance(distance, language, miles=miles)
                    )
                )
        elif distance is not None and km_per_day:
            start = today + timedelta(days=int(distance / km_per_day))
            estimated = True
            lines.append(
                strings["estimate"].format(
                    distance=format_distance(distance, language, miles=miles),
                    pace=format_distance(km_per_day, language, miles=miles),
                )
            )
        else:
            continue
        if (description := _text(item.get("description"))) is not None:
            lines.append(description)
        status = str(item.get("status") or "").upper()
        if status == "OVERDUE":
            summary = strings["overdue"].format(title=title)
        elif status == "PENDING":
            summary = strings["due_soon"].format(title=title)
        else:
            summary = title
        ident = item.get("id")
        uid = f"{vin}-cbs-{ident if ident is not None else title}"
        events.append(ServiceEvent(uid, summary, start, "\n".join(lines), estimated))
    events.sort(key=lambda event: (event.start, event.summary))
    return events


def next_event(events: Iterable[ServiceEvent], today: date) -> Optional[ServiceEvent]:
    """The event happening today or the next one after it, as HA's state shows."""

    upcoming = [event for event in events if event.end > today]
    return min(upcoming, key=lambda event: event.start, default=None)
