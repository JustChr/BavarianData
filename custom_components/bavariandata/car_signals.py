"""The descriptors the coordinator reads, and the timings it decides by.

Every name a car streams that the trip detector, the charging ledger or
the triggers act on, and every threshold they act with, in one place:
shared by the coordinator and the engines it hands work to, so none has to
import the coordinator. The *why* of each value is next to it; changing one
changes behaviour, and the golden gate (``scripts/golden.py``) will say so.
No Home Assistant imports.
"""

from __future__ import annotations

# Trip-detection descriptors (roadmap Phase 3). Motion is powertrain-agnostic and
# the cleanest start signal; ignition corroborates it; BMW's own completed-segment
# batch is the authoritative close and carries the per-trip statistics.
DESC_MOVING = "vehicle.isMoving"
DESC_IGNITION = "vehicle.drivetrain.engine.isIgnitionOn"
# Live position, the trip signal for cars that stream neither motion nor
# ignition. Latitude and longitude arrive as separate messages, so the current
# fix is read back from stored state rather than assumed present in one batch.
DESC_GPS_LAT = "vehicle.cabin.infotainment.navigation.currentLocation.latitude"
DESC_GPS_LON = "vehicle.cabin.infotainment.navigation.currentLocation.longitude"
# The cumulative odometer descriptor differs by model: most cars stream
# ``vehicle.vehicle.mileage``; the i5 streams ``vehicle.vehicle.travelledDistance``
# instead (same thing -- a lifetime total in km). Trying both in order means trip
# distance and charging-cost mileage come from BMW's own figure wherever either
# is present, falling back to the GPS track only when neither is.
DESC_ODOMETER = ("vehicle.vehicle.mileage", "vehicle.vehicle.travelledDistance")
# The fuel left, in litres: what a report trip's fuel figures are read from.
DESC_FUEL_L = "vehicle.drivetrain.fuelSystem.remainingFuel"
DESC_FUEL_LEVEL = "vehicle.drivetrain.fuelSystem.level"
# A US gallon, for a tank BMW reports in gallons; the fuel log keeps litres.
LITRES_PER_GALLON = 3.785411784
DESC_SEG_PREFIX = "vehicle.trip.segment.end."
DESC_SEG_DISTANCE = "vehicle.trip.segment.end.travelledDistance"
DESC_SEG_RECUP = "vehicle.trip.segment.accumulated.drivetrain.electricEngine.recuperationTotal"
DESC_SEG_CONSUMPTION = (
    "vehicle.trip.segment.accumulated.drivetrain.electricEngine.energyConsumptionComfort"
)
DESC_SEG_ACCEL_STARS = "vehicle.trip.segment.accumulated.acceleration.starsAverage"
DESC_SEG_BRAKE_STARS = "vehicle.trip.segment.accumulated.chassis.brake.starsAverage"
DESC_SEG_ECO = (
    "vehicle.trip.segment.accumulated.drivetrain.transmission.setting.fractionDriveEcoPro"
)
DESC_SEG_ELECTRIC = (
    "vehicle.trip.segment.accumulated.drivetrain.transmission.setting.fractionDriveElectric"
)
# Driver door. When a car streams it (the i5 does), it brackets a drive far more
# precisely than GPS jitter: the door closing means the driver just got in (a
# drive is imminent), and the door opening after the car has stopped means they
# have arrived. Used as an *accelerator* only -- GPS stays the fallback for cars
# that don't stream it -- so trip detection never depends on it.
DESC_DRIVER_DOOR = "vehicle.cabin.door.row1.driver.isOpen"

# GPS-quality descriptors, folded into every ``[trip.gps]`` capture line. They
# bear directly on the detector's worst failure mode: a run of "no movement"
# fixes that is really the *fix* being lost, not the car stopping, splits one
# drive in two. Seeing fix state / satellite count / heading alongside each step
# tells the two apart. Same ``currentLocation.*`` group as lat/lon, so a car that
# streams position very likely streams these too (when selected).
DESC_GPS_FIX = "vehicle.cabin.infotainment.navigation.currentLocation.fixStatus"
DESC_GPS_SATS = "vehicle.cabin.infotainment.navigation.currentLocation.numberOfSatellites"
DESC_GPS_HEADING = "vehicle.cabin.infotainment.navigation.currentLocation.heading"

# Once the car has been at rest this long with no new segment batch, treat the
# trip as ended. Long enough to ride out a traffic light, short enough that a
# parked car's trip closes promptly.
TRIP_CLOSE_DEBOUNCE_S = 300

# The debounce above assumes the fixes that stopped moving are evidence the *car*
# stopped. When the position stream simply goes quiet mid-drive -- the i5 can gap
# for minutes, and a tunnel or a bad cell patch longer still -- there is no such
# evidence, and closing on the timer alone splits one drive into two. So a close
# with no stop evidence behind it is *held*: the trip stays open until a fix comes
# back and settles it (a fix showing the car where it left off closes the trip; a
# fix showing it has moved on continues the same trip). This caps how long that
# wait may last, for a stream that never comes back. Generous, because the close
# is backdated to the last movement either way (see ``_trip_end_time``), so a late
# close costs latency, not accuracy.
TRIP_SILENT_HOLD_MAX_S = 1800

# A driver-door-close start marker is only honoured for opening a trip within
# this long -- so an unrelated door-close hours before a drive can't seed it.
PENDING_START_MAX_S = 300
# A driver-door-open ends an open trip only once the car has been stationary at
# least this long, so a door read that flickers open mid-move can't close a live
# drive. Short, because a driver opens their door only after actually stopping.
DOOR_ARRIVAL_STOP_S = 20
# How long a door-open waits for the rest of its snapshot when no fix has shown
# the car standing yet: an i4 sends the door ahead of its final fix (issue #183).
DOOR_SETTLE_S = 10
# An odometer or SoC reading that lands this soon after a trip closed, with the
# car not moving since, is that trip's: the i5 once sent its last km 52 s after
# the door ended the drive. Settled by the next fix (standing: the trip's; moving:
# the next drive's) or, with no fix at all, after LATE_READING_SETTLE_S.
LATE_READING_MAX_S = 300
LATE_READING_SETTLE_S = 60
# BMW streams latitude and longitude as two separate messages ~1 s apart, so a
# fix is only complete once *both* have arrived. Detection waits for that (see
# ``_gps_fix_ready``); this guard stops a frozen single component from stalling
# GPS processing forever if the pairing assumption ever fails.
GPS_PAIR_STALE_S = 120
# The two halves of a fix, tracked by arrival rather than by timestamp: waiting
# for both messages is the only pairing rule that cannot silently drift out of
# step (see ``_gps_fix_ready``).
GPS_PART_LAT = "lat"
GPS_PART_LON = "lon"
GPS_PAIR_PARTS = frozenset({GPS_PART_LAT, GPS_PART_LON})

# BMW's ``charging.status`` can briefly drop out of CHARGINGACTIVE mid-charge (a
# momentary NOCHARGING/PAUSED blip on the stream) and come straight back. Closing
# the session on the transition would split one plug-in into two records -- one
# then enriched by BMW's import, the other orphaned and double-counted -- so the
# close is debounced: if charging resumes within this window the same session
# continues instead. Long enough to ride out a flap, short enough that a genuine
# unplug still records promptly.
CHARGE_CLOSE_DEBOUNCE_S = 120

# How long a stopped charge waits for a resume while the cable is still in. Some
# cars stop for longer than the flap debounce mid-charge and carry on by
# themselves: a MINI Cooper SE on a portable charger paused for 2:39 and 2:03 and
# was filed as three charges (#180). With the plug still connected the session
# stays open this long after the stop; an unplug closes it at once, and a car
# that doesn't stream its plug keeps the plain debounce. The record still ends
# at the stop, so only the stop events wait.
CHARGE_PLUGGED_HOLD_S = 300

# How often an in-progress charge is written to the store. Every write rewrites
# the whole history document, so this is a trade between disk churn and how much
# of a charge a *crash* can cost -- a clean restart or reload loses nothing,
# because both snapshot on the way out. Five minutes of a 11 kW charge is under
# a kilowatt-hour, and the SoC-derived ceiling stops even that from inflating a
# resumed total beyond what the pack can hold.
OPEN_SESSION_SAVE_INTERVAL_S = 300

# How long a restored in-progress charge waits for the stream to say whether it
# is still running. BMW republishes ``charging.status`` on its own schedule --
# measured at seven minutes after one restart, and not at all after another
# where the charge had ended meanwhile -- so the wait has to be generous. When
# it expires the session is closed as what it already is, ending at its last
# sample rather than at the timeout.
RESTORED_SESSION_GRACE_S = 900

# How far the wallbox meter has to count past a restored charge's snapshot to
# show the charge outlived the restart. Above a meter's rounding, below what
# even a 1.4 kW trickle charge counts over the grace.
METER_MOVED_KWH = 0.1

# Charging statuses that mean this plug-in is over, as opposed to merely not
# delivering right now. BMW's enum is nocharging / initialization /
# chargingactive / chargingpaused / chargingended / chargingerror, and the
# middle two are *transient*: a charge that is handshaking or paused is still
# the same charge. Only used to decide the fate of a session restored across a
# restart -- a live session has the flap debounce for this, which a restored one
# cannot use because its gap has already happened. Anything unrecognised counts
# as transient and waits for the grace timer instead.
CHARGE_TERMINAL_STATUSES = frozenset({"NOCHARGING", "CHARGINGENDED", "CHARGINGERROR"})

# BMW's in-charge progress figures, which it simply stops sending when a charge
# ends instead of sending a zero: on the i5 the time to full stayed at 32 min and
# the power at 10.56 kW for good after a charge reached its target (2026-10-10).
# Zeroed when our own session closes on the car's word -- after the flap
# debounce, so a blip that comes straight back never zeroes a value BMW may not
# send again; and never on a restart's timeout, which can't know the charge ended.
CHARGE_PROGRESS_DESCRIPTORS = (
    "vehicle.drivetrain.electricEngine.charging.timeToFullyCharged",
    "vehicle.drivetrain.electricEngine.charging.timeRemaining",
    "vehicle.powertrain.electric.battery.charging.power",
)

# A ``trip.segment.end.*`` field is only a completed-trip signal if its own
# timestamp is recent: BMW ships the "last trip end" fields (e.g. ``hvSoc``) in
# every telematic snapshot with the *previous* drive's timestamp, so an old one
# would otherwise close a live GPS trip the moment the car parks and polls.
SEG_FRESH_S = 900

# --- What the efficiency profile reads live --------------------------------
# The measured consumption comes out of the charging ledger, but two of its
# inputs arrive as ordinary stream messages: BMW's own remaining range (the
# comparison the real-range entity and the card's efficiency view are built
# around) and the pack capacity that consumption is divided into. Neither moves
# the SoC estimate nor lands a history record, so anything that recomputes the
# profile only on those two signals must also watch these -- otherwise it keeps
# whatever it worked out when it was created, which right after a restart is
# before either descriptor has arrived.
DESC_BMW_RANGE = "vehicle.drivetrain.electricEngine.kombiRemainingElectricRange"
DESC_MAX_ENERGY = "vehicle.drivetrain.batteryManagement.maxEnergy"
EFFICIENCY_LIVE_DESCRIPTORS = (DESC_BMW_RANGE, DESC_MAX_ENERGY)

# --- What a charge controller is told (see bridge.py / evcc.py) ------------
# The charging status is already handled by name in the message paths above;
# named here because the bridge needs to know whether it has *ever* arrived,
# which is a different question from what it currently says.
DESC_CHARGE_STATUS = "vehicle.drivetrain.electricEngine.charging.status"
# Why a charge ended, most specific first (carried on the interrupted event).
CHARGE_END_REASON_DESCRIPTORS = (
    "vehicle.drivetrain.electricEngine.charging.reasonChargingEnd",
    "vehicle.drivetrain.electricEngine.charging.hvpmFinishReason",
)
# Whether a cable is in the car comes from a chain of descriptors that differs by
# model -- see ``evcc.PLUG_DESCRIPTORS``, where it is unit-tested.
