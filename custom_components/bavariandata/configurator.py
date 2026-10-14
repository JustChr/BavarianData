"""Pictures of the exact car from BMW's online configurator (opt-in, unofficial).

CarData's own vehicle image is a single side view. BMW's configurator draws any
car it has ever sold from three things the car already reports in its basic
data: the model code (``modelKey``), the paint (``colourCodeRaw``) and the
factory options (``fullSAList``). Its image service ("COSY") turns those into a
36-frame turntable with a transparent background.

That service is not part of CarData: it is the backend of BMW's public
configurator web page, so it is opt-in and may change without notice. Nothing
here spends CarData quota. The key the service wants is the configurator's
own browser key, read from the configurator's published settings file at fetch
time, because it changes whenever BMW redeploys the page.

Known traps, found by probing:

* A car renders under one brand name only (an i5 as ``bmwCar``, an i3 as
  ``bmwi``). The other name still answers with image links, but every one of
  them returns a grey "no picture" placeholder with HTTP 404. So each name is
  tried in turn, as the configurator itself does.
* ``effectdate`` is ``YYYYMMDD``; it is the build date, which also makes cars
  that are no longer sold render.
* ``background=TRANSPARENT`` only works on the ``dynamic-images`` endpoint.
* There is no overhead view anywhere; the camera is always at eye level.
* Basic data carries no upholstery code, so interiors would be a guess. Only
  exterior frames are fetched.

No Home Assistant imports: the rules are unit-tested directly.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Optional, Sequence

import aiohttp

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=30)

# Each configurator's public page, and the brand names its image service knows,
# in the order to try them. The page names its settings file, which holds the
# image service's address, account name ("mandator") and key.
_SITES = {
    "bmw": "https://configure.bmw.de",
    "mini": "https://configure.mini.de",
}
_BRANDS = {
    "BMW": ("bmw", ("bmwCar", "bmwi")),
    "BMWi": ("bmw", ("bmwi", "bmwCar")),
    "MINI": ("mini", ("mini",)),
}
_SETTINGS_RE = re.compile(r"settings\.([0-9a-f]{6,})\.json")
_COLLECTION = "walkaround-360"
# The turntable size fetched: BMW's 960x540 frames, scaled down to fit the card.
_SOURCE_VIEW = "mobile"
FRAME_SIZE = (640, 360)
SPRITE_COLUMNS = 6
# Frames are 10 degrees apart, starting head-on; frame 9 is the side profile the
# paint is sampled from, frame 4 the front three-quarter view used as the still.
SIDE_FRAME = 9
STILL_FRAME = 4
_CONCURRENCY = 6


class ConfiguratorError(Exception):
    """A picture could not be made. ``reason`` is a short, stable code."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


@dataclass(frozen=True)
class CarSpec:
    """What the configurator needs to draw one car."""

    site: str
    brands: tuple[str, ...]
    model: str
    options: tuple[str, ...]
    effect_date: str


@dataclass(frozen=True)
class Render:
    """A finished turntable: one sprite image, a still and the paint colour."""

    sprite: bytes
    sprite_type: str
    columns: int
    rows: int
    frame_width: int
    frame_height: int
    frames: int
    still: bytes
    paint: Optional[str]
    brand: str


def spec_from_basic_data(raw: Mapping[str, Any]) -> CarSpec:
    """Build the configurator request from a car's raw basic data.

    Raises ``ConfiguratorError("unsupported")`` for a brand the configurator
    does not draw (motorcycles, Supra, Rolls-Royce) and ``"no_data"`` when the
    model code or build date is missing.
    """

    brand = str(raw.get("brand") or "").strip()
    if brand not in _BRANDS:
        raise ConfiguratorError("unsupported", brand or "no brand")
    site, brands = _BRANDS[brand]
    model = str(raw.get("modelKey") or "").strip().upper()
    built = str(raw.get("constructionDate") or "")
    date = re.match(r"(\d{4})-(\d{2})-(\d{2})", built)
    if not re.fullmatch(r"[0-9A-Z]{4}", model) or not date:
        raise ConfiguratorError("no_data", "model code or build date missing")
    options: list[str] = []
    paint = str(raw.get("colourCodeRaw") or "").strip().upper()
    if re.fullmatch(r"[0-9A-Z]{3}", paint):
        options.append(f"P0{paint}")
    for code in str(raw.get("fullSAList") or "").split(","):
        code = code.strip().upper()
        if re.fullmatch(r"[0-9A-Z]{4}", code):
            options.append(f"S{code}")
    return CarSpec(site, brands, model, tuple(options), "".join(date.groups()))


def settings_path(page_html: str) -> Optional[str]:
    """The settings file a configurator page names, e.g. ``settings.bb566ca8.json``."""

    match = _SETTINGS_RE.search(page_html)
    return match.group(0) if match else None


def cosy_settings(settings: Mapping[str, Any]) -> dict:
    """The image service's address, account and key from a settings file."""

    try:
        cosy = settings["APP"]["backend"]["settingsCosy"]
        return {
            "base_url": str(cosy["baseUrl"]).rstrip("/"),
            "mandator": str(cosy["mandator"]),
            "hub": str(cosy.get("hub") or ""),
            "headers": {str(k): str(v) for k, v in (cosy.get("headers") or {}).items()},
        }
    except (KeyError, TypeError) as err:
        raise ConfiguratorError("unavailable", f"settings file changed: {err}") from err


def frame_urls(response: Mapping[str, Any]) -> list[str]:
    """The turntable's image links from the image service's answer."""

    try:
        views = response[_COLLECTION]
        block = views.get(_SOURCE_VIEW) or next(iter(views.values()))
        return [str(image["viewImage"]) for image in block["images"]]
    except (KeyError, TypeError, StopIteration, AttributeError) as err:
        raise ConfiguratorError("unavailable", f"unexpected answer: {err}") from err


def build_sprite(frames: Sequence[bytes], columns: int = SPRITE_COLUMNS) -> Render:
    """Scale the frames to ``FRAME_SIZE`` and lay them out in one grid image.

    One image means the card signs and loads a single URL for the whole spin.
    """

    from PIL import Image  # noqa: PLC0415 - Pillow is only needed once a picture is made

    if not frames:
        raise ConfiguratorError("no_render", "no frames")
    width, height = FRAME_SIZE
    rows = -(-len(frames) // columns)
    sources = [Image.open(io.BytesIO(data)).convert("RGBA") for data in frames]
    crop = _spin_box(sources, width / height)
    sprite = Image.new("RGBA", (width * columns, height * rows), (0, 0, 0, 0))
    images = []
    for index, source in enumerate(sources):
        frame = source.crop(crop).resize(FRAME_SIZE, Image.Resampling.LANCZOS)
        images.append(frame)
        sprite.paste(frame, ((index % columns) * width, (index // columns) * height))
    out = io.BytesIO()
    try:
        sprite.save(out, "WEBP", quality=82, method=4)
        sprite_type = "image/webp"
    except KeyError, OSError:  # a Pillow built without WebP
        out = io.BytesIO()
        sprite.save(out, "PNG", optimize=True)
        sprite_type = "image/png"
    still = io.BytesIO()
    sources[min(STILL_FRAME, len(sources) - 1)].crop(crop).save(still, "PNG")
    side = images[min(SIDE_FRAME, len(images) - 1)]
    return Render(
        sprite=out.getvalue(),
        sprite_type=sprite_type,
        columns=columns,
        rows=rows,
        frame_width=width,
        frame_height=height,
        frames=len(frames),
        still=still.getvalue(),
        paint=paint_color(side),
        brand="",
    )


def _spin_box(frames: Sequence[Any], aspect: float) -> tuple[int, int, int, int]:
    """One crop for every frame: the area the car covers anywhere in the spin.

    BMW's frames leave the car small in a wide empty canvas. A shared box (not
    one per frame) keeps the car from jumping while it turns. It is padded a
    little, widened or heightened to ``aspect`` and kept inside the canvas.
    """

    canvas_w, canvas_h = frames[0].size
    boxes = [f.getchannel("A").getbbox() for f in frames]
    boxes = [b for b in boxes if b]
    if not boxes:
        return (0, 0, canvas_w, canvas_h)
    left = min(b[0] for b in boxes)
    top = min(b[1] for b in boxes)
    right = max(b[2] for b in boxes)
    bottom = max(b[3] for b in boxes)
    pad = 0.06 * max(right - left, bottom - top)
    w, h = right - left + 2 * pad, bottom - top + 2 * pad
    if w / h < aspect:
        w = h * aspect
    else:
        h = w / aspect
    w, h = min(w, canvas_w), min(h, canvas_h)
    cx, cy = (left + right) / 2, (top + bottom) / 2
    x0 = min(max(cx - w / 2, 0), canvas_w - w)
    y0 = min(max(cy - h / 2, 0), canvas_h - h)
    return (round(x0), round(y0), round(x0 + w), round(y0 + h))


def paint_color(side_view: Any) -> Optional[str]:
    """The car's paint as ``#rrggbb``, sampled from a side-profile frame.

    Takes the opaque pixels of the door band (the middle of the car's outline,
    below the windows and above the sills) and their per-channel median, which
    shrugs off handles, trim and reflections.
    """

    image = side_view.convert("RGBA")
    box = image.getchannel("A").point(lambda a: 255 if a > 250 else 0).getbbox()
    if not box:
        return None
    left, top, right, bottom = box
    car_w, car_h = right - left, bottom - top
    band = image.crop(
        (
            left + int(car_w * 0.30),
            top + int(car_h * 0.52),
            left + int(car_w * 0.70),
            top + int(car_h * 0.72),
        )
    )
    # Raw RGBA bytes rather than getdata(), which Pillow 14 drops and whose
    # replacement older Pillow builds (as HA may ship) do not have.
    raw = band.tobytes()
    pixels = [raw[i : i + 3] for i in range(0, len(raw), 4) if raw[i + 3] > 250]
    if len(pixels) < 50:
        return None
    channels = []
    for i in range(3):
        values = sorted(p[i] for p in pixels)
        channels.append(values[len(values) // 2])
    return "#{:02x}{:02x}{:02x}".format(*channels)


async def _get(session: aiohttp.ClientSession, url: str, **kwargs: Any) -> tuple[int, bytes]:
    try:
        async with session.get(url, timeout=DEFAULT_TIMEOUT, **kwargs) as resp:
            return resp.status, await resp.read()
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        raise ConfiguratorError("unavailable", f"{type(err).__name__}") from err


async def async_fetch_settings(session: aiohttp.ClientSession, site: str) -> dict:
    """Read the image service's current address and key from the configurator."""

    import json  # noqa: PLC0415

    base = _SITES[site]
    status, body = await _get(session, f"{base}/de_DE/model-ranges")
    path = settings_path(body.decode("utf-8", "replace")) if status == 200 else None
    if not path:
        raise ConfiguratorError("unavailable", f"configurator page answered {status}")
    status, body = await _get(session, f"{base}/de_DE/{path}")
    if status != 200:
        raise ConfiguratorError("unavailable", f"settings file answered {status}")
    try:
        return cosy_settings(json.loads(body))
    except ValueError as err:
        raise ConfiguratorError("unavailable", "settings file is not JSON") from err


async def async_render(
    session: aiohttp.ClientSession,
    raw_basic_data: Mapping[str, Any],
    settings: Optional[Mapping[str, Any]] = None,
    run_sync: Optional[Callable[..., Awaitable[Any]]] = None,
) -> Render:
    """Fetch the turntable for one car and build its sprite.

    ``settings`` (from :func:`async_fetch_settings`) can be passed in to share
    one lookup across several cars of the same brand. ``run_sync`` runs the
    image work off the event loop (Home Assistant passes
    ``hass.async_add_executor_job``); without it, it runs inline.
    """

    import json  # noqa: PLC0415
    from urllib.parse import urlencode  # noqa: PLC0415

    spec = spec_from_basic_data(raw_basic_data)
    cosy = settings or await async_fetch_settings(session, spec.site)
    query = urlencode(
        [
            ("options", ",".join(spec.options)),
            ("effectdate", spec.effect_date),
            ("hub", cosy["hub"]),
            ("imagetype", "PNG"),
            ("background", "TRANSPARENT"),
        ]
    )
    semaphore = asyncio.Semaphore(_CONCURRENCY)

    async def frame(url: str) -> Optional[bytes]:
        async with semaphore:
            status, body = await _get(session, url)
        return body if status == 200 else None

    for brand in spec.brands:
        url = (
            f"{cosy['base_url']}/image-urls/dynamic-images/mandators/{cosy['mandator']}"
            f"/collections/{_COLLECTION}/brands/{brand}/models/{spec.model}?{query}"
        )
        status, body = await _get(session, url, headers=cosy["headers"])
        if status in (401, 403):
            raise ConfiguratorError("unavailable", f"image service refused the key ({status})")
        if status != 200:
            _LOGGER.debug("Configurator: %s/%s answered %s", brand, spec.model, status)
            continue
        try:
            urls = frame_urls(json.loads(body))
        except ValueError as err:
            raise ConfiguratorError("unavailable", "image service answer is not JSON") from err
        # The placeholder brand returns links that all 404: one probe decides it.
        first = await frame(urls[0]) if urls else None
        if first is None:
            _LOGGER.debug("Configurator: %s/%s draws only placeholders", brand, spec.model)
            continue
        rest = await asyncio.gather(*(frame(u) for u in urls[1:]))
        if any(r is None for r in rest):
            raise ConfiguratorError("no_render", "some frames are missing")
        frames = [first, *[r for r in rest if r is not None]]
        try:
            if run_sync is not None:
                render = await run_sync(build_sprite, frames)
            else:
                render = build_sprite(frames)
        except (OSError, ValueError) as err:  # a frame Pillow cannot read
            raise ConfiguratorError("no_render", f"unreadable frame: {err}") from err
        return Render(**{**render.__dict__, "brand": brand})
    raise ConfiguratorError("no_render", f"no picture for model {spec.model}")
