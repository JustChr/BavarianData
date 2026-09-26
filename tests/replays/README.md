# Replays

Every `*.ndjson` here is run through the real coordinator by
`tests/test_replays.py` and its outcome — trips, charging sessions, events and
the step-by-step timeline — is pinned to a snapshot.

A line is one raw MQTT batch as trip-capture mode writes it
(`<config>/bavariandata_trip_capture.ndjson`):
`{"at": <iso>, "vin": <vin>, "data": {<descriptor>: {"value", "unit", "timestamp"}}}`.

To turn a real day into a regression test:

1. Switch on **Configure → Trips → trip-capture diagnostics**, reproduce, switch
   it off again.
2. `python tools/anonymize_capture.py <capture> tests/replays/<name>.ndjson` —
   replaces the VIN, shifts positions and times, drops free text. **Read the
   output before committing it**; this repository is public.
3. `python -m pytest tests/test_replays.py --snapshot-update`, read what the
   integration made of it, fix, and watch the snapshot diff.

`synthetic-drive-tunnel-charge.ndjson` is hand-made in BMW's wire format (a
drive out of the Home zone with a ten-minute tunnel, then a charge) so the
path is exercised before any real capture lands here.
