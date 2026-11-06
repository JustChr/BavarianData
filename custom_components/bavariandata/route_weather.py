"""The weather along a trip's route, looked up from Open-Meteo once it is over.

CarData has no outside temperature, so consumption in the cold can only be
judged against the weather from outside. Open-Meteo serves 15-minute values for
any point (no key, no BMW quota), corrected to the ground height at that point
on a 90 m terrain model, and keeps them for past days too: the forecast API for
the last weeks, its historical-forecast archive for older ones.

During a drive the trip builder keeps **waypoints in memory only**: a GPS fix at
most every :data:`SAMPLE_INTERVAL_S` or :data:`SAMPLE_DISTANCE_KM`, rounded to
:data:`POINT_PRECISION` decimals (about 1 km), with the car's direction of
travel. After the trip closes, one request asks for the quarter-hour each
waypoint was passed. The trip stores the samples (time into the trip,
temperature, rain, wind, ground height, heading) and a time-weighted summary.
**No coordinate is stored**; the waypoints are dropped once asked.

Opt-in (``trip_weather``, off by default), because the route leaves the house.
Home Assistant-free: the sampler, the request and the parsing are plain
functions, unit-tested without an HA install; the one network call is thin and
never raises, since a missing forecast must never break trip recording.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence

import aiohttp

_LOGGER = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HISTORICAL_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
# The forecast API serves about three months back; older trips go to the archive.
FORECAST_MAX_AGE = timedelta(days=80)
TIMEOUT = aiohttp.ClientTimeout(total=20)

# ~1 km: Open-Meteo's finest models (2 km in central Europe) resolve no closer,
# and the terrain correction is taken at the point actually sent.
POINT_PRECISION = 2
SAMPLE_INTERVAL_S = 300
SAMPLE_DISTANCE_KM = 5.0
# One request's locations. A longer drive is thinned evenly, ends kept.
MAX_POINTS = 60

VARIABLES = ("temperature_2m", "precipitation", "wind_speed_10m", "wind_direction_10m")
QUARTER = timedelta(minutes=15)

# Where a trip's waypoints came from: its drive (``route``), its stored track
# (``track``), only its two ends (``ends``), or the home location (``home``).
SOURCES = ("route", "track", "ends", "home")


@dataclass(frozen=True)
class WeatherPoint:
    """One place the car passed, rounded, and when. Never persisted."""

    at: datetime
    lat: float
    lon: float
    # Direction of travel towards this point in degrees (0 = north), if known.
    heading: Optional[float] = None


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return round((math.degrees(math.atan2(x, y)) + 360) % 360)


@dataclass
class WaypointSampler:
    """Keeps the waypoints of one drive: at most one per 5 min or 5 km."""

    points: list[WeatherPoint] = field(default_factory=list)
    # The last fix kept, unrounded, so a heading is measured on real positions.
    _last: Optional[tuple[datetime, float, float]] = None
    # The newest fix seen, kept or not, so the drive's end can be added.
    _newest: Optional[tuple[datetime, float, float]] = None

    def add(self, lat: float, lon: float, at: datetime) -> None:
        self._newest = (at, lat, lon)
        if self._last is not None:
            last_at, last_lat, last_lon = self._last
            moved = _km(last_lat, last_lon, lat, lon)
            if (at - last_at).total_seconds() < SAMPLE_INTERVAL_S and moved < SAMPLE_DISTANCE_KM:
                return
        self._keep(at, lat, lon)

    def finish(
        self, lat: Optional[float], lon: Optional[float], at: datetime
    ) -> list[WeatherPoint]:
        """The waypoints with the drive's end added: where it stopped, or the last fix."""

        if lat is not None and lon is not None:
            self._newest = (at, lat, lon)
        if self._newest is not None and not self._repeats_last(*self._newest):
            self._keep(*self._newest)
        return list(self.points)

    def _repeats_last(self, at: datetime, lat: float, lon: float) -> bool:
        """Whether this fix is the last kept one again: same spot, within the interval."""

        if self._last is None:
            return False
        last_at, last_lat, last_lon = self._last
        return (at - last_at).total_seconds() < SAMPLE_INTERVAL_S and _km(
            last_lat, last_lon, lat, lon
        ) <= 0.05

    def _keep(self, at: datetime, lat: float, lon: float) -> None:
        heading = None
        if self._last is not None and _km(self._last[1], self._last[2], lat, lon) > 0.05:
            heading = _bearing(self._last[1], self._last[2], lat, lon)
        self.points.append(
            WeatherPoint(
                at=at,
                lat=round(lat, POINT_PRECISION),
                lon=round(lon, POINT_PRECISION),
                heading=heading,
            )
        )
        self._last = (at, lat, lon)


def points_from_track(
    track: Sequence[Sequence[float]], start: datetime, end: Optional[datetime]
) -> list[WeatherPoint]:
    """Waypoints along a stored route, for a trip that ended before the lookup.

    A track point is ``[lat, lon, t]`` (``t`` seconds since the start); an old
    ``[lat, lon]`` track has no times, so its points are spread over the drive.
    """

    if not track:
        return []
    timed = all(len(point) >= 3 for point in track)
    span = ((end or start) - start).total_seconds()
    sampler = WaypointSampler()
    for index, point in enumerate(track):
        if timed:
            at = start + timedelta(seconds=float(point[2]))
        else:
            at = start + timedelta(seconds=span * index / max(1, len(track) - 1))
        sampler.add(float(point[0]), float(point[1]), at)
    return sampler.finish(None, None, end or start)


def points_from_ends(
    start_point: Optional[tuple[float, float]],
    end_point: Optional[tuple[float, float]],
    start: datetime,
    end: Optional[datetime],
) -> list[WeatherPoint]:
    """Waypoints at a trip's two ends only, where those are known."""

    points: list[WeatherPoint] = []
    if start_point is not None:
        points.append(
            WeatherPoint(
                start,
                round(start_point[0], POINT_PRECISION),
                round(start_point[1], POINT_PRECISION),
            )
        )
    if end_point is not None:
        points.append(
            WeatherPoint(
                end or start,
                round(end_point[0], POINT_PRECISION),
                round(end_point[1], POINT_PRECISION),
            )
        )
    return points


def thin(points: Sequence[WeatherPoint], limit: int = MAX_POINTS) -> list[WeatherPoint]:
    """At most ``limit`` points, spread evenly, the first and last kept."""

    if len(points) <= limit:
        return list(points)
    step = (len(points) - 1) / (limit - 1)
    return [points[round(i * step)] for i in range(limit)]


def _quarter(at: datetime) -> datetime:
    at = at.astimezone(timezone.utc).replace(second=0, microsecond=0)
    return at - timedelta(minutes=at.minute % 15)


def _stamp(at: datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M")


def build_request(points: Sequence[WeatherPoint], *, now: datetime) -> tuple[str, dict[str, str]]:
    """The URL and query for one lookup of every point's quarter-hour."""

    quarters = [_quarter(point.at) for point in points]
    oldest = min(quarters)
    url = FORECAST_URL if now - oldest <= FORECAST_MAX_AGE else HISTORICAL_URL
    params = {
        "latitude": ",".join(f"{point.lat:.{POINT_PRECISION}f}" for point in points),
        "longitude": ",".join(f"{point.lon:.{POINT_PRECISION}f}" for point in points),
        "minutely_15": ",".join(VARIABLES),
        "start_minutely_15": _stamp(oldest),
        "end_minutely_15": _stamp(max(quarters)),
        "timezone": "GMT",
    }
    return url, params


def _number(values: Any, index: int) -> Optional[float]:
    if not isinstance(values, list) or index >= len(values):
        return None
    value = values[index]
    return float(value) if isinstance(value, (int, float)) else None


def parse_samples(
    payload: Any, points: Sequence[WeatherPoint], start: datetime
) -> list[dict[str, Any]]:
    """One sample per point that Open-Meteo answered with a temperature.

    One location comes back as an object, several as a list in the order sent.
    """

    locations = payload if isinstance(payload, list) else [payload]
    samples: list[dict[str, Any]] = []
    for point, location in zip(points, locations, strict=False):
        if not isinstance(location, dict):
            continue
        series = location.get("minutely_15") or {}
        times = series.get("time") or []
        stamp = _stamp(_quarter(point.at))
        if stamp not in times:
            continue
        index = times.index(stamp)
        temperature = _number(series.get("temperature_2m"), index)
        if temperature is None:
            continue
        elevation = location.get("elevation")
        samples.append(
            {
                "t": max(0, int((point.at - start).total_seconds())),
                "temp_c": temperature,
                "rain_mm": _number(series.get("precipitation"), index),
                "wind_kmh": _number(series.get("wind_speed_10m"), index),
                "wind_dir": _number(series.get("wind_direction_10m"), index),
                "elevation_m": float(elevation) if isinstance(elevation, (int, float)) else None,
                "heading": point.heading,
            }
        )
    samples.sort(key=lambda sample: sample["t"])
    return samples


def summarize(samples: Sequence[dict[str, Any]], source: str) -> dict[str, Any]:
    """The trip's weather record: the samples and a time-weighted temperature.

    Each sample stands for the time halfway to its neighbours, so a dense patch
    of waypoints (a slow stretch in town) does not outweigh the open road. An
    answer without a single temperature is still a record (``temp_c`` ``None``),
    so the trip isn't asked about again.
    """

    if not samples:
        return {
            "source": source,
            "temp_c": None,
            "temp_min_c": None,
            "temp_max_c": None,
            "samples": [],
        }
    temps = [sample["temp_c"] for sample in samples]
    if len(samples) == 1:
        mean = temps[0]
    else:
        times = [sample["t"] for sample in samples]
        weights = []
        for i in range(len(samples)):
            left = times[i] - times[i - 1] if i > 0 else 0
            right = times[i + 1] - times[i] if i < len(samples) - 1 else 0
            weights.append((left + right) / 2)
        total = sum(weights)
        mean = (
            sum(t * w for t, w in zip(temps, weights, strict=True)) / total
            if total > 0
            else sum(temps) / len(temps)
        )
    return {
        "source": source,
        "temp_c": round(mean, 1),
        "temp_min_c": round(min(temps), 1),
        "temp_max_c": round(max(temps), 1),
        "samples": list(samples),
    }


class RouteWeather:
    """Asks Open-Meteo for the weather at a trip's waypoints. Off by default."""

    def __init__(self, session: aiohttp.ClientSession, *, enabled: bool = False) -> None:
        self._session = session
        self.enabled = enabled

    async def lookup(
        self,
        points: Sequence[WeatherPoint],
        start: datetime,
        *,
        source: str,
        now: datetime,
    ) -> Optional[dict[str, Any]]:
        """The trip's weather record, or ``None`` when there was no answer."""

        if not self.enabled or not points:
            return None
        points = thin(points)
        url, params = build_request(points, now=now)
        try:
            async with self._session.get(url, params=params, timeout=TIMEOUT) as response:
                if response.status != 200:
                    _LOGGER.debug("Open-Meteo answered HTTP %s for a trip", response.status)
                    return None
                payload = await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            _LOGGER.debug("Open-Meteo lookup failed: %s", err)
            return None
        return summarize(parse_samples(payload, points, start), source)
