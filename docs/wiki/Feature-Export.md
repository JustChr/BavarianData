# Export (CSV / HTML report)

The [Charging](The-Dashboard-Card#charging-history-view-charging) and
[Trips](The-Dashboard-Card#trips--driving-journal-view-trips) card views each
carry **CSV** and **Report** buttons that download the current month straight
from the browser. Both are backed by the **`bavariandata.export_history`**
service, so automations can use them too. Nothing is written to disk — the file
contents come back as service response data — and it spends **no API quota**.

## CSV

One row per session or trip:

- Both the **battery-side** and **measured grid** energy in separate columns.
- The cost and where it came from.
- For trips, the endpoints as **place names**.

Headers are always machine-readable English. Opens cleanly in Excel and Numbers.

## HTML report

A self-contained HTML page: month totals, the charging table and the trip
journal. Print it (**Ctrl/⌘+P → Save as PDF**) for a tidy A4 document. The
report language follows Home Assistant's own language, or set it explicitly.

> The report is a trip journal and expense helper, **not** a tax-office-compliant
> logbook, and says so on the page.

## Calling it directly

```yaml
service: bavariandata.export_history
data:
  month: "2026-07"     # defaults to the current month
  type: both           # charging · trips · both
  format: csv          # csv · html
  language: de          # en · de (HTML report only; defaults to HA's language)
```

See the [Services reference](Services-Reference#export_history) for the full
field list.
