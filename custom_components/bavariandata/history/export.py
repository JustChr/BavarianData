"""CSV and printable-HTML exports of recorded history.

Home Assistant-free (see ``models.py``): an export is pure formatting over the
records, so it belongs in the tested half of the history layer rather than in a
service handler.

Two formats, deliberately no PDF. A real PDF would mean adding a rendering
dependency to every install for a feature few users touch; a self-contained HTML
report prints to PDF from any browser and can be made to look considerably
better than anything we would draw by hand.

Labels are baked in here for both languages rather than threaded through
``translations/``: the same choice the bundled Lovelace card makes, and it keeps
the module a pure function of its arguments.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime
from html import escape
from typing import Any, Callable, Iterable, Optional

from .models import ChargingSession
from .trips import Trip

# Local-time converter, as in ``summary.py``; identity when the caller has none.
Localizer = Callable[[datetime], datetime]

# Excel on Windows only recognises a UTF-8 CSV if it starts with a byte-order
# mark -- without it, every umlaut in a German place name arrives mangled.
BOM = "﻿"

MIME_CSV = "text/csv"
MIME_HTML = "text/html"

STRINGS: dict[str, dict[str, str]] = {
    "en": {
        "report_title": "Driving and charging report",
        "month": "Month",
        "vehicle": "Vehicle",
        "generated": "Generated",
        "charging": "Charging",
        "trips": "Trips",
        "no_charging": "No charging sessions recorded for this month.",
        "no_trips": "No trips recorded for this month.",
        "sessions": "Sessions",
        "energy": "Energy",
        "cost": "Cost",
        "distance": "Distance",
        "trip_count": "Trips",
        "consumption": "Avg consumption",
        # The table column carries its unit; the tile puts it in the value.
        "consumption_col": "Consumption (kWh/100 km)",
        "recuperation": "Recuperated",
        "cost_per_100km": "Cost per 100 km",
        "business": "Business",
        "private": "Private",
        "commute": "Commute",
        "unclassified": "Unclassified",
        "date": "Date",
        "start": "Start",
        "end": "End",
        "duration": "Duration",
        "location": "Location",
        "soc": "SoC",
        "peak": "Peak",
        "from": "From",
        "to": "To",
        "class": "Class",
        "assumed": "assumed",
        "estimated": "estimated",
        "total": "Total",
        "disclaimer": (
            "This is a trip journal and expense helper, not a tax-compliant "
            "logbook. It carries none of the tamper-resistance or timeliness "
            "guarantees a tax authority requires."
        ),
        "footnote_partial": (
            "At least one cost figure is a floor: part of that session's energy "
            "was charged while no price was known."
        ),
        "source": "Recorded by BavarianData from the BMW CarData stream.",
    },
    "de": {
        "report_title": "Fahrt- und Ladebericht",
        "month": "Monat",
        "vehicle": "Fahrzeug",
        "generated": "Erstellt",
        "charging": "Laden",
        "trips": "Fahrten",
        "no_charging": "Für diesen Monat sind keine Ladevorgänge erfasst.",
        "no_trips": "Für diesen Monat sind keine Fahrten erfasst.",
        "sessions": "Ladevorgänge",
        "energy": "Energie",
        "cost": "Kosten",
        "distance": "Strecke",
        "trip_count": "Fahrten",
        "consumption": "Ø Verbrauch",
        "consumption_col": "Verbrauch (kWh/100 km)",
        "recuperation": "Rekuperiert",
        "cost_per_100km": "Kosten pro 100 km",
        "business": "Geschäftlich",
        "private": "Privat",
        "commute": "Pendeln",
        "unclassified": "Nicht zugeordnet",
        "date": "Datum",
        "start": "Start",
        "end": "Ende",
        "duration": "Dauer",
        "location": "Ort",
        "soc": "Ladestand",
        "peak": "Spitze",
        "from": "Von",
        "to": "Nach",
        "class": "Kategorie",
        "assumed": "angenommen",
        "estimated": "geschätzt",
        "total": "Gesamt",
        "disclaimer": (
            "Dies ist ein Fahrtenjournal und eine Kostenhilfe, kein "
            "finanzamtstaugliches Fahrtenbuch. Die gesetzlich geforderte "
            "Manipulationssicherheit und Zeitnähe kann es nicht zusichern."
        ),
        "footnote_partial": (
            "Mindestens ein Kostenwert ist eine Untergrenze: ein Teil der "
            "Energie wurde geladen, während kein Preis bekannt war."
        ),
        "source": "Aufgezeichnet von BavarianData aus dem BMW-CarData-Stream.",
    },
}


def _strings(lang: Optional[str]) -> dict[str, str]:
    return STRINGS.get((lang or "en")[:2].lower(), STRINGS["en"])


def _identity(value: datetime) -> datetime:
    return value


def _fmt_dt(
    value: Optional[datetime], localize: Localizer, *, time_only: bool = False
) -> str:
    if value is None:
        return ""
    local = localize(value)
    return local.strftime("%H:%M" if time_only else "%Y-%m-%d %H:%M")


def _minutes(seconds: Optional[int]) -> str:
    return "" if seconds is None else str(round(seconds / 60))


def _num(value: Optional[float], digits: int = 2) -> str:
    """A plain machine-readable number: dot decimals, no thousands separator.

    German spreadsheets will want commas, but a CSV that survives a round trip
    through a script matters more than one that opens pretty in one locale.
    """

    if value is None:
        return ""
    return f"{float(value):.{digits}f}".rstrip("0").rstrip(".") or "0"


def _classification(trip: Trip, strings: dict[str, str]) -> str:
    return strings.get(trip.classification or "", strings["unclassified"])


def _write(rows: Iterable[Iterable[Any]]) -> str:
    buffer = io.StringIO()
    # Excel treats a bare "\n" CSV as a single line on some Windows builds.
    writer = csv.writer(buffer, lineterminator="\r\n")
    for row in rows:
        writer.writerow(list(row))
    return BOM + buffer.getvalue()


def sessions_csv(
    sessions: Iterable[ChargingSession], *, localize: Localizer = _identity
) -> str:
    """One row per charging session, newest first.

    Both energy columns are exported side by side rather than collapsed into
    one: ``energy_kwh`` is battery-side and ``grid_kwh`` is measured, and a
    spreadsheet that hides which is which invites exactly the wrong conclusion.
    """

    header = [
        "id",
        "vin",
        "start",
        "end",
        "duration_min",
        "zone",
        "location_assumed",
        "soc_start",
        "soc_end",
        "soc_delta",
        "energy_kwh_battery",
        "grid_kwh_measured",
        "avg_power_kw",
        "peak_power_kw",
        "cost",
        "currency",
        "cost_source",
        "cost_partial",
        "odometer_km",
        "enriched",
    ]
    rows: list[list[Any]] = [header]
    for session in sessions:
        cost = session.cost or {}
        rows.append(
            [
                session.id,
                session.vin,
                _fmt_dt(session.start, localize),
                _fmt_dt(session.end, localize),
                _minutes(session.duration_s),
                (session.location or {}).get("zone") or "",
                "yes" if session.location_assumed else "no",
                _num(session.soc_start, 1),
                _num(session.soc_end, 1),
                _num(session.soc_delta, 1),
                _num(session.energy_kwh, 3),
                _num(session.grid_kwh, 3),
                _num(session.avg_power_kw, 3),
                _num(session.peak_power_kw, 2),
                _num(cost.get("amount"), 2),
                cost.get("currency") or "",
                cost.get("source") or "",
                "yes" if cost.get("partial") else "no",
                _num(session.mileage_km, 1),
                "yes" if session.enriched else "no",
            ]
        )
    return _write(rows)


def trips_csv(trips: Iterable[Trip], *, localize: Localizer = _identity) -> str:
    """One row per trip, newest first. Endpoints are place names, never coordinates."""

    header = [
        "id",
        "vin",
        "start",
        "end",
        "duration_min",
        "from",
        "to",
        "distance_km",
        "soc_start",
        "soc_end",
        "energy_kwh",
        "consumption_kwh_per_100km",
        "classification",
        "classification_source",
        "location_assumed",
    ]
    rows: list[list[Any]] = [header]
    for trip in trips:
        rows.append(
            [
                trip.id,
                trip.vin,
                _fmt_dt(trip.start, localize),
                _fmt_dt(trip.end, localize),
                _minutes(trip.duration_s),
                (trip.start_place or {}).get("label") or "",
                (trip.end_place or {}).get("label") or "",
                _num(trip.distance_km, 1),
                _num(trip.soc_start, 1),
                _num(trip.soc_end, 1),
                _num(trip.energy_kwh, 2),
                _num(trip.consumption_kwh_per_100km, 1),
                trip.classification or "",
                trip.classification_source or "",
                "yes" if trip.location_assumed else "no",
            ]
        )
    return _write(rows)


# --- printable report ------------------------------------------------------

_REPORT_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 32px;
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  color: #16181d; background: #f6f7f9;
}
.sheet {
  max-width: 900px; margin: 0 auto; background: #fff; border-radius: 12px;
  padding: 36px 40px; box-shadow: 0 1px 3px rgba(0,0,0,.09);
}
header { border-bottom: 3px solid #16181d; padding-bottom: 18px; margin-bottom: 26px; }
h1 { margin: 0 0 4px; font-size: 22px; letter-spacing: -.01em; }
.meta { color: #61656e; font-size: 13px; }
.meta b { color: #16181d; font-weight: 600; }
h2 {
  font-size: 12px; text-transform: uppercase; letter-spacing: .09em;
  color: #61656e; margin: 32px 0 12px; font-weight: 700;
}
/* Grid rather than flex-wrap: every tile keeps the same width, so a second
   row lines up under the first instead of stretching to fill. */
.tiles {
  display: grid; gap: 10px; margin-bottom: 22px;
  grid-template-columns: repeat(auto-fill, minmax(148px, 1fr));
}
.tile { background: #f2f3f5; border-radius: 9px; padding: 12px 14px; }
.tile .k { font-size: 11px; color: #61656e; text-transform: uppercase; letter-spacing: .05em; }
.tile .v { font-size: 18px; font-weight: 650; margin-top: 3px; letter-spacing: -.015em; }
.tile .v small { font-size: 12px; font-weight: 500; color: #61656e; }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
th {
  text-align: left; font-weight: 600; color: #61656e; font-size: 11px;
  text-transform: uppercase; letter-spacing: .05em;
  border-bottom: 1px solid #d9dbe0; padding: 0 10px 7px 0;
}
td { padding: 8px 10px 8px 0; border-bottom: 1px solid #eceef1; vertical-align: top; }
/* Right-aligned columns need their gutter on the left, or a numeric column
   butts straight into whatever follows it. */
th.n, td.n { text-align: right; padding-left: 14px; }
tr > *:last-child { padding-right: 0; }
tfoot td { font-weight: 650; border-bottom: none; border-top: 2px solid #16181d; }
.sub { color: #8a8e97; font-size: 11px; }
.badge {
  display: inline-block; padding: 1px 7px; border-radius: 20px; font-size: 11px;
  background: #eceef1; color: #41454d; white-space: nowrap;
}
.empty { color: #8a8e97; font-style: italic; padding: 10px 0; }
footer {
  margin-top: 34px; padding-top: 16px; border-top: 1px solid #eceef1;
  color: #8a8e97; font-size: 11px;
}
footer p { margin: 0 0 6px; }
@media print {
  body { padding: 0; background: #fff; }
  .sheet { box-shadow: none; border-radius: 0; padding: 0; max-width: none; }
  h2 { break-after: avoid; }
  tr { break-inside: avoid; }
  @page { margin: 16mm; }
}
"""


def _tile(key: str, value: str, note: str = "") -> str:
    suffix = f" <small>{escape(note)}</small>" if note else ""
    return (
        f'<div class="tile"><div class="k">{escape(key)}</div>'
        f'<div class="v">{escape(value)}{suffix}</div></div>'
    )


def _money(amount: Optional[float], currency: Optional[str]) -> str:
    if amount is None:
        return "—"
    return f"{amount:.2f} {currency or ''}".strip()


def month_report_html(
    *,
    month: str,
    vehicle: str,
    sessions: Iterable[ChargingSession],
    trips: Iterable[Trip],
    charging_summary: Optional[dict[str, Any]] = None,
    driving: Optional[dict[str, Any]] = None,
    lang: Optional[str] = "en",
    localize: Localizer = _identity,
    now: Optional[datetime] = None,
) -> str:
    """A self-contained month report, styled for the browser's print-to-PDF.

    Takes the already-computed summaries rather than recomputing them, so the
    report and the card can never disagree about a month's totals.
    """

    s = _strings(lang)
    sessions = list(sessions)
    trips = list(trips)
    charging_summary = charging_summary or {}
    driving = driving or {}
    currency = charging_summary.get("currency")

    parts: list[str] = [
        "<!-- BavarianData month report; print this page to get a PDF -->",
        f'<meta charset="utf-8"><title>{escape(vehicle)} · {escape(month)}</title>',
        f"<style>{_REPORT_CSS}</style>",
        '<div class="sheet"><header>',
        f"<h1>{escape(s['report_title'])}</h1>",
        f'<div class="meta"><b>{escape(vehicle)}</b> · {escape(s["month"])} '
        f"{escape(month)} · {escape(s['generated'])} "
        f"{escape(_fmt_dt(now, localize)) if now else ''}</div>",
        "</header>",
    ]

    # --- charging ---------------------------------------------------------
    parts.append(f"<h2>{escape(s['charging'])}</h2>")
    if sessions:
        tiles = [
            _tile(s["sessions"], str(charging_summary.get("sessions", len(sessions)))),
            _tile(s["energy"], f"{charging_summary.get('energy_kwh', 0)} kWh"),
        ]
        if charging_summary.get("cost") is not None:
            tiles.append(
                _tile(
                    s["cost"],
                    _money(charging_summary["cost"], currency),
                    s["estimated"] if charging_summary.get("partial") else "",
                )
            )
        if charging_summary.get("cost_per_100km") is not None:
            tiles.append(
                _tile(
                    s["cost_per_100km"],
                    _money(charging_summary["cost_per_100km"], currency),
                )
            )
        parts.append(f'<div class="tiles">{"".join(tiles)}</div>')

        head = "".join(
            f'<th class="n">{escape(label)}</th>' if numeric else f"<th>{escape(label)}</th>"
            for label, numeric in (
                (s["date"], False),
                (s["location"], False),
                (s["duration"], True),
                (s["soc"], True),
                (s["energy"], True),
                (s["peak"], True),
                (s["cost"], True),
            )
        )
        rows: list[str] = []
        for item in sessions:
            zone = (item.location or {}).get("zone") or "—"
            if item.location_assumed:
                zone = f'{escape(zone)} <span class="sub">({escape(s["assumed"])})</span>'
            else:
                zone = escape(zone)
            soc = (
                f"{item.soc_start:.0f} → {item.soc_end:.0f}%"
                if item.soc_start is not None and item.soc_end is not None
                else "—"
            )
            energy = item.grid_kwh if item.grid_kwh is not None else item.energy_kwh
            cost = (item.cost or {}).get("amount")
            rows.append(
                "<tr>"
                f"<td>{escape(_fmt_dt(item.start, localize))}</td>"
                f"<td>{zone}</td>"
                f'<td class="n">{escape(_minutes(item.duration_s))} min</td>'
                f'<td class="n">{escape(soc)}</td>'
                f'<td class="n">{escape(_num(energy, 2) or "—")} kWh</td>'
                f'<td class="n">{escape(_num(item.peak_power_kw, 1) or "—")} kW</td>'
                f'<td class="n">{escape(_money(cost, currency))}</td>'
                "</tr>"
            )
        total_cost = charging_summary.get("cost")
        foot = (
            "<tfoot><tr>"
            f'<td colspan="4">{escape(s["total"])}</td>'
            f'<td class="n">{escape(_num(charging_summary.get("energy_kwh"), 2) or "—")} kWh</td>'
            "<td></td>"
            f'<td class="n">{escape(_money(total_cost, currency))}</td>'
            "</tr></tfoot>"
        )
        parts.append(
            f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody>{foot}</table>"
        )
    else:
        parts.append(f'<div class="empty">{escape(s["no_charging"])}</div>')

    # --- trips ------------------------------------------------------------
    parts.append(f"<h2>{escape(s['trips'])}</h2>")
    if trips:
        split = driving.get("split") or {}
        tiles = [
            _tile(s["distance"], f"{driving.get('total_km', 0)} km"),
            _tile(s["trip_count"], str(driving.get("trip_count", len(trips)))),
        ]
        if driving.get("avg_consumption_kwh_per_100km") is not None:
            # The unit rides as the small note: spelled out at tile size it is
            # the one value long enough to wrap onto a second line.
            tiles.append(
                _tile(
                    s["consumption"],
                    str(driving["avg_consumption_kwh_per_100km"]),
                    "kWh/100 km",
                )
            )
        if driving.get("recuperation_kwh") is not None:
            tiles.append(_tile(s["recuperation"], f"{driving['recuperation_kwh']} kWh"))
        for key, label in (
            ("business_km", s["business"]),
            ("commute_km", s["commute"]),
            ("private_km", s["private"]),
        ):
            if split.get(key):
                tiles.append(_tile(label, f"{split[key]} km"))
        parts.append(f'<div class="tiles">{"".join(tiles)}</div>')

        head = "".join(
            f'<th class="n">{escape(label)}</th>' if numeric else f"<th>{escape(label)}</th>"
            for label, numeric in (
                (s["date"], False),
                (s["from"], False),
                (s["to"], False),
                (s["duration"], True),
                (s["distance"], True),
                (s["consumption_col"], True),
                (s["class"], False),
            )
        )
        rows = []
        for trip in trips:
            rows.append(
                "<tr>"
                f"<td>{escape(_fmt_dt(trip.start, localize))}</td>"
                f'<td>{escape((trip.start_place or {}).get("label") or "—")}</td>'
                f'<td>{escape((trip.end_place or {}).get("label") or "—")}</td>'
                f'<td class="n">{escape(_minutes(trip.duration_s))} min</td>'
                f'<td class="n">{escape(_num(trip.distance_km, 1) or "—")} km</td>'
                f'<td class="n">{escape(_num(trip.consumption_kwh_per_100km, 1) or "—")}</td>'
                f'<td><span class="badge">{escape(_classification(trip, s))}</span></td>'
                "</tr>"
            )
        foot = (
            "<tfoot><tr>"
            f'<td colspan="4">{escape(s["total"])}</td>'
            f'<td class="n">{escape(str(driving.get("total_km", "")))} km</td>'
            "<td></td><td></td>"
            "</tr></tfoot>"
        )
        parts.append(
            f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody>{foot}</table>"
        )
    else:
        parts.append(f'<div class="empty">{escape(s["no_trips"])}</div>')

    parts.append("<footer>")
    if charging_summary.get("partial"):
        parts.append(f"<p>{escape(s['footnote_partial'])}</p>")
    parts.append(f"<p>{escape(s['disclaimer'])}</p>")
    parts.append(f"<p>{escape(s['source'])}</p>")
    parts.append("</footer></div>")
    return "\n".join(parts)
