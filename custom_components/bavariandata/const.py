"""Constants for the BMW CarData integration."""

DOMAIN = "bavariandata"
DEFAULT_SCOPE = "authenticate_user openid cardata:api:read cardata:streaming:read"
DEVICE_CODE_URL = "https://customer.bmwgroup.com/gcdm/oauth/device/code"
TOKEN_URL = "https://customer.bmwgroup.com/gcdm/oauth/token"
API_BASE_URL = "https://api-cardata.bmwgroup.com"
API_VERSION = "v1"
BASIC_DATA_ENDPOINT = "/customers/vehicles/{vin}/basicData"
DEFAULT_STREAM_HOST = "customer.streaming-cardata.bmwgroup.com"
DEFAULT_STREAM_PORT = 9000
DEFAULT_REFRESH_INTERVAL = 45 * 60  #How often to refresh the auth tokens in seconds
MQTT_KEEPALIVE = 30
DEBUG_LOG = False
DIAGNOSTIC_LOG_INTERVAL = 30 # How often we print stream logs in seconds
BOOTSTRAP_COMPLETE = "bootstrap_complete"
REQUEST_LOG = "request_log"
REQUEST_LOG_VERSION = 1
REQUEST_LIMIT = 50 # API Quota
REQUEST_WINDOW_SECONDS = 24 * 60 * 60 # How long API Quota is reserved after API Call in seconds
# How often to call the Telematic API, in seconds. Once a day: the container now
# holds the fields BMW *cannot* stream, and those change on the scale of days
# (service demands, tread wear, lifetime counters), not minutes. A 24 h cadence
# spends 1 of the 50 daily requests instead of the 36 the old 40-minute loop did,
# leaving the quota free for the fetch_* services.
# The tyre diagnosis rides the same loop on its own endpoint (one more request).
TELEMATIC_POLL_INTERVAL = 24 * 60 * 60
VEHICLE_METADATA = "vehicle_metadata"
OPTION_MQTT_KEEPALIVE = "mqtt_keepalive"
OPTION_DEBUG_LOG = "debug_log"
OPTION_DIAGNOSTIC_INTERVAL = "diagnostic_log_interval"
# Selected stream clusters (list of catalogue section slugs). When present, the
# device-code / refresh flows request granular cardata:streaming:<descriptor>
# scopes for those clusters instead of the coarse DEFAULT_SCOPE.
OPTION_STREAM_SECTIONS = "stream_sections"

# Charging-cost settings. Cost entities only exist once a mode other than
# "none" is chosen -- showing a wrong price is worse than showing none.
OPTION_PRICE_MODE = "price_mode"          # none | fixed | entity
OPTION_PRICE_FIXED = "price_fixed"        # currency units per kWh
OPTION_PRICE_ENTITY = "price_entity"      # a live price sensor (Tibber, Nordpool, ...)
OPTION_PRICE_CURRENCY = "price_currency"
# Optional wallbox energy sensor: a measured grid figure always beats the
# battery-side energy we integrate from the stream.
OPTION_GRID_ENERGY_ENTITY = "grid_energy_entity"
# Gross up battery-side energy by the AC charging losses. Defaults to 0 so we
# never dress an estimate up as a measurement.
OPTION_CHARGING_LOSS_PERCENT = "charging_loss_percent"
# How long recorded sessions are kept. 0 means "keep forever" (the hard
# per-VIN cap in history/store.py still applies).
OPTION_HISTORY_RETAIN_MONTHS = "history_retain_months"
DEFAULT_HISTORY_RETAIN_MONTHS = 24

# Trips (roadmap Phase 3). The work zone drives commute classification; the
# geocode toggle is off by default because it sends coordinates to OpenStreetMap.
OPTION_TRIP_WORK_ZONE = "trip_work_zone"
OPTION_TRIP_GEOCODE = "trip_geocode"
# What a trip that isn't a recognised home <-> work commute is classified as.
# Every automatic classification is a *default* the user can correct from the
# card, so labelling beats leaving drives blank -- but "unclassified" stays
# available for anyone who would rather triage each trip by hand.
OPTION_TRIP_DEFAULT_CLASS = "trip_default_class"
DEFAULT_TRIP_DEFAULT_CLASS = "private"
# How long the car may stand between two drives for both to still count as one
# commute -- the supermarket stop on the way to work. 0 turns chaining off.
# Stops shorter than the detector's own close debounce never split a drive in the
# first place, so this governs the band above it (see history/classify.py).
OPTION_TRIP_COMMUTE_GAP = "trip_commute_gap"
DEFAULT_TRIP_COMMUTE_GAP_MIN = 30
# Record the GPS track (route polyline) of each trip so a map can draw where --
# and, from the per-point timestamp, when -- the car went. Off by default and
# independent of geocoding: it is the one trip setting that persists raw
# coordinates to disk, so it stays strictly opt-in. When off, trips keep storing
# named places only -- never coordinates.
OPTION_TRIP_TRACK = "trip_track"
# Trip-capture debug mode (diagnostic, off by default). Independent of the
# generic ``debug_log`` toggle: when on, the coordinator emits a rich, greppable
# capture of the trip detector's raw substrate (every GPS fix, the close-timer
# lifecycle, full segment batches, a per-message descriptor firehose and a
# per-trip post-mortem) and mirrors each raw MQTT batch to an NDJSON file, so a
# single test drive yields the data to design a better detector. Verbose and
# PII-heavy (raw GPS/VIN) -- meant to be switched on for a drive and back off.
OPTION_TRIP_DEBUG = "trip_debug"

# Statistics backfill (roadmap Phase 4). On by default: it writes only into this
# integration's own "bavariandata:" statistic namespace, stays local, and is what
# puts charging that predates the install onto the Energy dashboard. Turning it
# off deletes the series we published.
OPTION_STATISTICS_IMPORT = "statistics_import"
DEFAULT_STATISTICS_IMPORT = True

# Dispatcher signal fired when a vehicle render is (re)cached, carrying the VIN.
SIGNAL_VEHICLE_IMAGE = f"{DOMAIN}_vehicle_image_updated"

# Home Assistant bus events fired on meaningful charging transitions so
# automations can trigger on a semantic moment rather than polling state. Each
# carries {"vin", "entry_id", ...} in its data payload.
EVENT_CHARGING_STARTED = f"{DOMAIN}_charging_started"
EVENT_CHARGING_STOPPED = f"{DOMAIN}_charging_stopped"
EVENT_CHARGING_COMPLETE = f"{DOMAIN}_charging_complete"

# Bundled Lovelace card. The JS is served as a static path and auto-registered as
# a frontend resource so users don't have to add it manually.
LOVELACE_CARD_FILENAME = "bavariandata-card.js"
LOVELACE_CARD_URL = f"/{DOMAIN}/{LOVELACE_CARD_FILENAME}"

HV_BATTERY_CONTAINER_NAME = "BMW CarData HV Battery"
HV_BATTERY_CONTAINER_PURPOSE = "High voltage battery telemetry"
HV_BATTERY_DESCRIPTORS = [
    # Current high-voltage battery state of charge
    "vehicle.drivetrain.batteryManagement.header",
    "vehicle.drivetrain.electricEngine.charging.acAmpere",
    "vehicle.drivetrain.electricEngine.charging.acVoltage",
    "vehicle.powertrain.electric.battery.preconditioning.automaticMode.statusFeedback",
    "vehicle.vehicle.avgAuxPower",
    "vehicle.powertrain.tractionBattery.charging.port.anyPosition.flap.isOpen",
    "vehicle.powertrain.tractionBattery.charging.port.anyPosition.isPlugged",
    "vehicle.drivetrain.electricEngine.charging.timeToFullyCharged",
    "vehicle.powertrain.electric.battery.charging.acLimit.selected",
    "vehicle.drivetrain.electricEngine.charging.method",
    "vehicle.body.chargingPort.plugEventId",
    "vehicle.drivetrain.electricEngine.charging.phaseNumber",
    "vehicle.trip.segment.end.drivetrain.batteryManagement.hvSoc",
    "vehicle.trip.segment.accumulated.drivetrain.electricEngine.recuperationTotal",
    "vehicle.drivetrain.electricEngine.remainingElectricRange",
    "vehicle.drivetrain.electricEngine.charging.timeRemaining",
    "vehicle.drivetrain.electricEngine.charging.hvStatus",
    "vehicle.drivetrain.electricEngine.charging.lastChargingReason",
    "vehicle.drivetrain.electricEngine.charging.lastChargingResult",
    "vehicle.powertrain.electric.battery.preconditioning.manualMode.statusFeedback",
    "vehicle.drivetrain.electricEngine.charging.reasonChargingEnd",
    "vehicle.powertrain.electric.battery.stateOfCharge.target",
    "vehicle.body.chargingPort.lockedStatus",
    "vehicle.drivetrain.electricEngine.charging.level",
    "vehicle.powertrain.electric.battery.stateOfHealth.displayed",
    "vehicle.vehicleIdentification.basicVehicleData",
    "vehicle.drivetrain.batteryManagement.batterySizeMax",
    "vehicle.drivetrain.batteryManagement.maxEnergy",
    "vehicle.powertrain.electric.battery.charging.power",
    "vehicle.drivetrain.electricEngine.charging.status"

]

# Response shapes for the mapping, telematic and basic-data endpoints are
# documented in docs/reference/customer-api.swagger.json (BMW's own schema).

