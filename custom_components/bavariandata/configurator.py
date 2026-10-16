"""Pictures and equipment of the exact car from BMW's online configurator (opt-in).

CarData's own vehicle image is a single side view. BMW's configurator draws any
car it has ever sold from three things the car already reports in its basic
data: the model code (``modelKey``), the paint (``colourCodeRaw``) and the
factory options (``fullSAList``). From those this module fetches:

* a 36-frame turntable with a transparent background, three times over: plain,
  with the lights on, and as the night scene (cut out with the plain spin, so
  the card draws the night backdrop);
* a 360-degree interior panorama (six cube faces);
* a true top view, which BMW draws for MINIs only;
* the names of the factory options, i.e. the car's equipment list;
* a small picture of each option that has one (a paint or wheel swatch, a photo
  of the feature), all in one sheet.

None of it is part of CarData: it is the backend of BMW's public configurator
web page, so it is opt-in and may change without notice. Nothing here spends
CarData quota. The keys the services want are the configurator's own browser
keys, read from its published settings file at fetch time, because they change
whenever BMW redeploys the page.

Known traps, found by probing:

* A car renders under one brand name only (an i5 as ``bmwCar``, an i3 as
  ``bmwi``). The other name still answers with image links, but every one of
  them returns a grey "no picture" placeholder with HTTP 404. So each name is
  tried in turn, as the configurator itself does.
* ``effectdate`` is ``YYYYMMDD`` for images and ``YYYY-MM-DD`` for option
  names; it is the build date, which also makes cars no longer sold render.
* ``background=TRANSPARENT`` only works on the ``dynamic-images`` endpoint,
  and not for the night scenes. ``background=night`` is MINI-only: a BMW
  answers it with plain white.
* The exterior camera is always at eye level; only MINIs have a top view.
* Basic data carries no upholstery code, so the interior shows BMW's default
  trim, which may not be the car's.
* Option names exist only in the language of the market that sells the model
  (German for Germany and Austria, French for France); other markets use other
  model codes, so a German-market car has no English names.
* Every image request refuses an ``options`` list that isn't sorted or names
  a code twice (HTTP 400, ``options is not sorted``). Cars report theirs in
  no particular order, so it is sorted on the way out.
* Option pictures: the service answers every code with two links, a tile and
  ``static_wide``; at most one is real (a swatch for paint, wheels and roof, a
  photo for the rest). The tile is ``tile_wide`` for MINI and ``tile_square``
  for BMW, though both are the same 168 x 95. The other, and both for technical codes, return a brand
  logo with HTTP 404, which must never be kept as the option's picture.

No Home Assistant imports: the rules are unit-tested directly.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
from dataclasses import dataclass, field, replace
from typing import Any, Awaitable, Callable, Mapping, Optional, Sequence
from urllib.parse import urlencode

import aiohttp

_LOGGER = logging.getLogger(__name__)

DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=30)

# Each configurator's public page, and the brand names its image service knows,
# in the order to try them. The page names its settings file, which holds the
# services' addresses, account names and keys.
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
_SPIN = "walkaround-360"
# The night spin, asked for the way each configurator asks: BMW draws its cars
# at night only as ``nightview`` (a black studio, the car in shadow), while
# MINI's own night scene is ``background=night`` with the lights on (the car
# lit from the front). Both come back on an opaque scene, which ``cut_out``
# replaces with transparency so the card can draw one backdrop for every car.
_NIGHT_SCENE: dict[str, tuple[tuple[tuple[str, str], ...], str]] = {
    "bmw": ((("nightview", "true"),), "TRANSPARENT"),
    "mini": ((("lightson", "true"),), "night"),
}
_INTERIOR = "interior-mixed"
_TOP = "roof-and-mirror-caps"
# The turntable size fetched: BMW's 960x540 frames, scaled down to fit the card.
_SOURCE_VIEW = "mobile"
FRAME_SIZE = (640, 360)
SPRITE_COLUMNS = 6
# Frames are 10 degrees apart, starting head-on; frame 9 is the side profile the
# paint is sampled from, frame 4 the front three-quarter view used as the still.
SIDE_FRAME = 9
STILL_FRAME = 4
# The interior's six cube faces, scaled from BMW's 2000 px and laid out 3 x 2.
FACE_SIZE = 1024
# The option pictures: BMW's own tile size, laid out in one sheet.
_OPTIONS = "unified-sgt-options"
_OPTION_VIEWS = ("static_wide", "tile_wide", "tile_square")
OPTION_TILE = (168, 95)
OPTION_COLUMNS = 8
_CONCURRENCY = 6
# The language an option name is asked for first in a market, after the
# caller's own. A market answers in its own language only.
_MARKET_LANGUAGE = {
    "at": "de",
    "ch": "de",
    "de": "de",
    "li": "de",
    "lu": "fr",
    "be": "fr",
    "fr": "fr",
    "it": "it",
    "es": "es",
    "nl": "nl",
    "pl": "pl",
    "pt": "pt",
    "cz": "cs",
    "se": "sv",
    "dk": "da",
    "no": "no",
    "fi": "fi",
    "gb": "en",
    "ie": "en",
    "us": "en",
    "ca": "en",
    "au": "en",
}


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
    effect_date: str  # YYYYMMDD

    @property
    def iso_date(self) -> str:
        d = self.effect_date
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"

    @property
    def sorted_options(self) -> str:
        """The ``options`` parameter as the image service accepts it."""

        return ",".join(sorted(self.options))


@dataclass(frozen=True)
class Render:
    """Everything fetched for one car. Only the plain turntable is required."""

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
    lights: Optional[bytes] = None
    night: Optional[bytes] = None
    interior: Optional[bytes] = None
    top: Optional[bytes] = None
    equipment: tuple[dict, ...] = field(default_factory=tuple)
    equipment_language: Optional[str] = None
    # The option pictures' sheet, and the code of each tile in sheet order.
    options: Optional[bytes] = None
    option_codes: tuple[str, ...] = ()
    # The spin's crop box, so the lights-on and night spins line up with it.
    crop: Optional[tuple[int, int, int, int]] = None


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
    # Once each, in the car's order (the equipment list shows them that way).
    return CarSpec(site, brands, model, tuple(dict.fromkeys(options)), "".join(date.groups()))


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


def names_settings(settings: Mapping[str, Any]) -> Optional[dict]:
    """The option-name service's address and key, or None if the file lacks it."""

    try:
        loc = settings["APP"]["backend"]["settingsLocalisation"]
        return {
            "base_url": str(loc["baseUrl"]).rstrip("/"),
            "source": str(loc["source"]),
            "brand": str(loc["brand"]),
            "application": str(loc["application"]),
            "headers": {str(k): str(v) for k, v in (loc.get("headers") or {}).items()},
        }
    except KeyError, TypeError:
        return None


def frame_urls(response: Mapping[str, Any], collection: str = _SPIN) -> list[str]:
    """The image links from the image service's answer, in display order."""

    try:
        views = response[collection]
        block = views.get(_SOURCE_VIEW) or next(iter(views.values()))
        images = sorted(block["images"], key=lambda i: i.get("displayOrder", 0))
        return [str(image["viewImage"]) for image in images]
    except (KeyError, TypeError, StopIteration, AttributeError) as err:
        raise ConfiguratorError("unavailable", f"unexpected answer: {err}") from err


def option_image_urls(response: Any) -> dict[str, list[str]]:
    """Each option code's picture links from the image service, likeliest first.

    Both views are kept, because only fetching tells which one is real. A
    changed or empty answer gives ``{}``: the pictures are an extra.
    """

    try:
        views = response["collections"][_OPTIONS]
        block = views.get("desktop") or next(iter(views.values()))
        entries = block.items()
    except KeyError, TypeError, StopIteration, AttributeError:
        return {}
    out: dict[str, list[str]] = {}
    for code, entry in entries:
        found: dict[str, str] = {}
        groups = entry.get("imageGroups") if isinstance(entry, Mapping) else None
        for group in groups if isinstance(groups, list) else []:
            if not isinstance(group, Mapping):
                continue
            view = (group.get("distinctionParams") or {}).get("VIEW")
            images = group.get("images") or []
            link = images[0].get("viewImage") if images and isinstance(images[0], Mapping) else None
            if view in _OPTION_VIEWS and isinstance(link, str) and link:
                found[view] = link
        links = [found[view] for view in _OPTION_VIEWS if view in found]
        if links:
            out[str(code)] = links
    return out


def _save(image: Any, *, alpha: bool = True) -> tuple[bytes, str]:
    out = io.BytesIO()
    try:
        image.save(out, "WEBP", quality=82, method=4)
        return out.getvalue(), "image/webp"
    except KeyError, OSError:  # a Pillow built without WebP
        out = io.BytesIO()
        (image if alpha else image.convert("RGB")).save(out, "PNG", optimize=True)
        return out.getvalue(), "image/png"


def build_sprite(
    frames: Sequence[bytes],
    columns: int = SPRITE_COLUMNS,
    crop: Optional[tuple[int, int, int, int]] = None,
) -> Render:
    """Scale the frames to ``FRAME_SIZE`` and lay them out in one grid image.

    One image means the card signs and loads a single URL for the whole spin.
    ``crop`` reuses another spin's box, so the lights-on and night spins line up
    frame for frame with the plain one (their backgrounds aren't transparent).
    """

    from PIL import Image  # noqa: PLC0415 - Pillow is only needed once a picture is made

    if not frames:
        raise ConfiguratorError("no_render", "no frames")
    width, height = FRAME_SIZE
    rows = -(-len(frames) // columns)
    sources = [Image.open(io.BytesIO(data)).convert("RGBA") for data in frames]
    box = crop or _spin_box(sources, width / height)
    sprite = Image.new("RGBA", (width * columns, height * rows), (0, 0, 0, 0))
    images = []
    for index, source in enumerate(sources):
        frame = source.crop(box).resize(FRAME_SIZE, Image.Resampling.LANCZOS)
        images.append(frame)
        sprite.paste(frame, ((index % columns) * width, (index // columns) * height))
    data, sprite_type = _save(sprite)
    still = io.BytesIO()
    sources[min(STILL_FRAME, len(sources) - 1)].crop(box).save(still, "PNG")
    side = images[min(SIDE_FRAME, len(images) - 1)]
    render = Render(
        sprite=data,
        sprite_type=sprite_type,
        columns=columns,
        rows=rows,
        frame_width=width,
        frame_height=height,
        frames=len(frames),
        still=still.getvalue(),
        paint=paint_color(side),
        brand="",
        crop=box,
    )
    return render


def cut_out(sprite: bytes, stencil: bytes) -> bytes:
    """``sprite`` with the transparency of ``stencil``, the plain spin's sprite.

    The night scenes are drawn on an opaque studio (BMW's with stepped rings
    of floor light), but they share their frames and crop with the plain spin,
    whose car is already cut out. Taking its alpha leaves the night car alone.
    A stencil without any transparency (a car BMW draws on white) would blank
    nothing useful, so the sprite is returned as it was.
    """

    from PIL import Image  # noqa: PLC0415 - Pillow is only needed once a picture is made

    mask = Image.open(io.BytesIO(stencil)).convert("RGBA").getchannel("A")
    if mask.getextrema()[0] == 255:
        return sprite
    image = Image.open(io.BytesIO(sprite)).convert("RGBA")
    if image.size != mask.size:
        return sprite
    image.putalpha(mask)
    return _save(image)[0]


def build_interior(faces: Sequence[bytes]) -> bytes:
    """The six interior cube faces, scaled and laid out 3 x 2 in display order."""

    from PIL import Image  # noqa: PLC0415

    if len(faces) != 6:
        raise ConfiguratorError("no_render", f"{len(faces)} interior faces")
    sheet = Image.new("RGB", (FACE_SIZE * 3, FACE_SIZE * 2))
    for index, data in enumerate(faces):
        face = (
            Image.open(io.BytesIO(data))
            .convert("RGB")
            .resize((FACE_SIZE, FACE_SIZE), Image.Resampling.LANCZOS)
        )
        sheet.paste(face, ((index % 3) * FACE_SIZE, (index // 3) * FACE_SIZE))
    return _save(sheet, alpha=False)[0]


def build_top(data: bytes) -> bytes:
    """A top view on white, cut out, cropped and turned to face up the card.

    BMW draws it nose-left on a white page. The white connected to the edges is
    made transparent (so the roof's own highlights stay), then the car is
    cropped to its outline and turned a quarter so the nose points up, as on
    the card's own top-down drawing.
    """

    from PIL import Image, ImageChops, ImageDraw  # noqa: PLC0415

    image = Image.open(io.BytesIO(data)).convert("RGBA")
    width, height = image.size
    # Flood the page from each white corner with a marker colour, then key the
    # marker out: what's left opaque is the car, roof highlights included.
    marker = (255, 0, 255)
    rgb = image.convert("RGB")
    for corner in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
        if min(rgb.getpixel(corner)) > 240:
            ImageDraw.floodfill(rgb, corner, marker, thresh=18)
    diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, marker))
    alpha = diff.convert("L").point(lambda v: 255 if v else 0)
    image.putalpha(alpha)
    box = alpha.getbbox()
    if not box:
        raise ConfiguratorError("no_render", "empty top view")
    image = image.crop(box).rotate(-90, expand=True)
    return _save(image)[0]


def build_option_sheet(
    pictures: Sequence[tuple[str, bytes]], columns: int = OPTION_COLUMNS
) -> tuple[bytes, list[str]]:
    """The option pictures in one sheet, in the order given; returns it and the codes.

    One image means the card signs and loads a single URL for the whole list.
    BMW's tiles are all ``OPTION_TILE``; any other size is fitted in, centred.
    A picture Pillow cannot read is left out rather than failing the sheet.
    """

    from PIL import Image  # noqa: PLC0415

    width, height = OPTION_TILE
    tiles = []
    for code, data in pictures:
        try:
            image = Image.open(io.BytesIO(data)).convert("RGBA")
        except OSError, ValueError:
            continue
        if image.size != OPTION_TILE:
            image.thumbnail(OPTION_TILE, Image.Resampling.LANCZOS)
            tile = Image.new("RGBA", OPTION_TILE, (0, 0, 0, 0))
            tile.paste(image, ((width - image.width) // 2, (height - image.height) // 2))
            image = tile
        tiles.append((code, image))
    if not tiles:
        raise ConfiguratorError("no_render", "no option pictures")
    rows = -(-len(tiles) // columns)
    sheet = Image.new("RGBA", (width * columns, height * rows), (0, 0, 0, 0))
    for index, (_code, image) in enumerate(tiles):
        sheet.paste(image, ((index % columns) * width, (index // columns) * height))
    return _save(sheet)[0], [code for code, _image in tiles]


def equipment_list(names: Mapping[str, Any], spec: CarSpec) -> list[dict]:
    """The car's options with names, in the car's own order, paint first.

    ``kind`` is BMW's option type (``paint``, ``rim``, ``trim``, ``package`` or
    ``other``). ``shown`` is False for what BMW itself treats as technical
    (``displayTypeId`` 2 or 3: refrigerant, paperwork language, emission
    controls); the card folds those away. Codes the service doesn't know are
    left out.
    """

    out = []
    for code in spec.options:
        item = names.get(code)
        if not isinstance(item, Mapping):
            continue
        phrases = item.get("phrases") or {}
        name = phrases.get("longDescription") or phrases.get("shortDescription")
        if not isinstance(name, str) or not name.strip():
            continue
        try:
            display = int(item.get("displayTypeId") or 1)
        except TypeError, ValueError:
            display = 1
        out.append(
            {
                "code": code,
                "name": " ".join(name.split()),
                "kind": str(item.get("optionType") or "other"),
                "shown": display == 1,
            }
        )
    return out


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


def _why(body: bytes) -> str:
    """The image service's own words for a refused request."""

    try:
        message = json.loads(body).get("errorMessage")
    except ValueError, AttributeError:
        message = None
    return str(message) if message else body[:120].decode("utf-8", "replace")


async def _get(session: aiohttp.ClientSession, url: str, **kwargs: Any) -> tuple[int, bytes]:
    try:
        async with session.get(url, timeout=DEFAULT_TIMEOUT, **kwargs) as resp:
            return resp.status, await resp.read()
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        raise ConfiguratorError("unavailable", f"{type(err).__name__}") from err


async def async_fetch_settings(session: aiohttp.ClientSession, site: str) -> dict:
    """Read the services' current addresses and keys from the configurator.

    Returns ``{"cosy": ..., "names": ... or None}``.
    """

    base = _SITES[site]
    status, body = await _get(session, f"{base}/de_DE/model-ranges")
    path = settings_path(body.decode("utf-8", "replace")) if status == 200 else None
    if not path:
        raise ConfiguratorError("unavailable", f"configurator page answered {status}")
    status, body = await _get(session, f"{base}/de_DE/{path}")
    if status != 200:
        raise ConfiguratorError("unavailable", f"settings file answered {status}")
    try:
        settings = json.loads(body)
    except ValueError as err:
        raise ConfiguratorError("unavailable", "settings file is not JSON") from err
    return {"cosy": cosy_settings(settings), "names": names_settings(settings)}


async def async_fetch_equipment(
    session: aiohttp.ClientSession,
    spec: CarSpec,
    names: Mapping[str, Any],
    country: Optional[str],
    languages: Sequence[str] = (),
) -> tuple[list[dict], Optional[str]]:
    """Name the car's options in the first language its market answers in.

    Tries the car's market (from basic data) with the caller's languages first
    and the market's own language last, then the German market, where the
    configurator lives. Returns ``([], None)`` when no market knows the model.
    """

    markets = [c for c in dict.fromkeys([(country or "").lower(), "de"]) if c]
    for market in markets:
        wanted = [*languages, _MARKET_LANGUAGE.get(market, "")]
        for language in [lang for lang in dict.fromkeys(wanted) if lang]:
            url = (
                f"{names['base_url']}/localisations/overridden-vehicle-data/sources/{names['source']}"
                f"/brands/{names['brand']}/countries/{market}/effect-dates/{spec.iso_date}"
                f"/order-dates/{spec.iso_date}/applications/{names['application']}"
                f"/models/{spec.model}/options/languages/{language}"
            )
            status, body = await _get(session, url, headers=names["headers"])
            if status != 200:
                continue
            try:
                found = equipment_list(json.loads(body), spec)
            except ValueError:
                continue
            if found:
                return found, language
    return [], None


async def async_render(
    session: aiohttp.ClientSession,
    raw_basic_data: Mapping[str, Any],
    settings: Optional[Mapping[str, Any]] = None,
    run_sync: Optional[Callable[..., Awaitable[Any]]] = None,
    languages: Sequence[str] = (),
    extras: bool = True,
) -> Render:
    """Fetch everything for one car: spins, interior, top view, equipment, option pictures.

    Only the plain turntable must succeed; the rest is added when BMW has it.
    ``settings`` (from :func:`async_fetch_settings`) can be passed in to share
    one lookup across several cars of the same brand. ``run_sync`` runs the
    image work off the event loop (Home Assistant passes
    ``hass.async_add_executor_job``); without it, it runs inline.
    ``languages`` are tried first for the equipment names. ``extras=False``
    stops after the plain turntable (about 5 s rather than about a minute), for
    a settings screen that shouldn't keep the user waiting.
    """

    spec = spec_from_basic_data(raw_basic_data)
    found = settings or await async_fetch_settings(session, spec.site)
    cosy = found["cosy"]
    semaphore = asyncio.Semaphore(_CONCURRENCY)

    async def run(func: Callable[..., Any], *args: Any) -> Any:
        try:
            if run_sync is not None:
                return await run_sync(func, *args)
            return func(*args)
        except (OSError, ValueError) as err:  # an image Pillow cannot read
            raise ConfiguratorError("no_render", f"unreadable image: {err}") from err

    async def image(url: str) -> Optional[bytes]:
        async with semaphore:
            status, body = await _get(session, url)
        return body if status == 200 else None

    def query(*extra: tuple[str, str]) -> str:
        return urlencode(
            [
                ("options", spec.sorted_options),
                ("effectdate", spec.effect_date),
                ("hub", cosy["hub"]),
                ("imagetype", "PNG"),
                *extra,
            ]
        )

    async def links(
        endpoint: str, collection: str, brand: str, *extra: tuple[str, str]
    ) -> Optional[Any]:
        url = (
            f"{cosy['base_url']}/image-urls/{endpoint}/mandators/{cosy['mandator']}"
            f"/collections/{collection}/brands/{brand}/models/{spec.model}?{query(*extra)}"
        )
        status, body = await _get(session, url, headers=cosy["headers"])
        if status in (401, 403):
            raise ConfiguratorError("unavailable", f"image service refused the key ({status})")
        if status == 400:
            # Our request is wrong, not the brand: the other brand would be too.
            raise ConfiguratorError("no_render", f"image service refused the request: {_why(body)}")
        if status != 200:
            _LOGGER.debug(
                "Configurator: %s %s/%s answered %s", collection, brand, spec.model, status
            )
            return None
        try:
            return json.loads(body)
        except ValueError as err:
            raise ConfiguratorError("unavailable", "image service answer is not JSON") from err

    async def spin(
        brand: str, *extra: tuple[str, str], background: str = "TRANSPARENT"
    ) -> Optional[list[bytes]]:
        answer = await links("dynamic-images", _SPIN, brand, ("background", background), *extra)
        urls = frame_urls(answer) if answer else []
        # The placeholder brand returns links that all 404: one probe decides it.
        first = await image(urls[0]) if urls else None
        if first is None:
            return None
        rest = await asyncio.gather(*(image(u) for u in urls[1:]))
        if any(r is None for r in rest):
            raise ConfiguratorError("no_render", "some frames are missing")
        return [first, *[r for r in rest if r is not None]]

    for brand in spec.brands:
        frames = await spin(brand)
        if frames is None:
            _LOGGER.debug("Configurator: %s/%s draws only placeholders", brand, spec.model)
            continue
        render = await run(build_sprite, frames)
        crop = render.crop
        if not extras:
            return replace(render, brand=brand)
        extras_found: dict[str, Any] = {"brand": brand}
        try:
            variant = await spin(brand, ("lightson", "true"))
            if variant:
                extras_found["lights"] = (
                    await run(build_sprite, variant, SPRITE_COLUMNS, crop)
                ).sprite
        except ConfiguratorError as err:
            _LOGGER.debug("Configurator: no lights spin for %s: %s", spec.model, err)
        try:
            switches, background = _NIGHT_SCENE[spec.site]
            variant = await spin(brand, *switches, background=background)
            if variant:
                night = await run(build_sprite, variant, SPRITE_COLUMNS, crop)
                extras_found["night"] = await run(cut_out, night.sprite, render.sprite)
        except ConfiguratorError as err:
            _LOGGER.debug("Configurator: no night spin for %s: %s", spec.model, err)
        extras_found.update(await _optional_extras(spec, brand, links, image, run))
        extras_found.update(
            await _option_pictures(
                session, cosy, spec, brand, raw_basic_data.get("countryCode"), image, run
            )
        )
        names = found.get("names")
        if names:
            try:
                equipment, language = await async_fetch_equipment(
                    session, spec, names, raw_basic_data.get("countryCode"), languages
                )
                extras_found.update(equipment=tuple(equipment), equipment_language=language)
            except ConfiguratorError as err:
                _LOGGER.debug("Configurator: no equipment names for %s: %s", spec.model, err)
        return replace(render, **extras_found)
    raise ConfiguratorError("no_render", f"no picture for model {spec.model}")


async def _optional_extras(
    spec: CarSpec,
    brand: str,
    links: Callable[..., Awaitable[Any]],
    image: Callable[[str], Awaitable[Optional[bytes]]],
    run: Callable[..., Awaitable[Any]],
) -> dict:
    """The interior panorama and the top view, when BMW has them for this car."""

    out: dict[str, Any] = {}
    try:
        answer = await links("dynamic-images", _INTERIOR, brand)
        if answer:
            views = answer.get(_INTERIOR) or {}
            block = views.get("res_2000x2000") or {}
            faces_meta = sorted(block.get("images") or [], key=lambda i: i.get("displayOrder", 0))
            faces = [await image(str(f["viewImage"])) for f in faces_meta]
            if len(faces) == 6 and all(faces):
                out["interior"] = await run(build_interior, faces)
    except (ConfiguratorError, KeyError, TypeError, AttributeError) as err:
        _LOGGER.debug("Configurator: no interior for %s: %s", spec.model, err)
    try:
        answer = await links("dynamic-named-images", _TOP, brand)
        views = (answer or {}).get(_TOP) or {}
        block = views.get("desktop") or next(iter(views.values()), {})
        for named in block.get("namedImages") or []:
            for item in named.get("images") or []:
                if (item.get("distinctionParameters") or {}).get("POV") == "TOP":
                    data = await image(str(item["viewImage"]))
                    if data:  # BMWs answer with a placeholder 404 here
                        out["top"] = await run(build_top, data)
    except (ConfiguratorError, KeyError, TypeError, AttributeError) as err:
        _LOGGER.debug("Configurator: no top view for %s: %s", spec.model, err)
    return out


async def _option_pictures(
    session: aiohttp.ClientSession,
    cosy: Mapping[str, Any],
    spec: CarSpec,
    brand: str,
    country: Any,
    image: Callable[[str], Awaitable[Optional[bytes]]],
    run: Callable[..., Awaitable[Any]],
) -> dict:
    """A small picture of each option BMW has one for, laid out in one sheet."""

    market = str(country or "").strip().lower()
    query = urlencode(
        [
            ("effectdate", spec.effect_date),
            ("hub", cosy["hub"]),
            ("imagetype", "webp"),
            ("options", spec.sorted_options),
            *([("params.market", market)] if market else []),
        ]
    )
    url = (
        f"{cosy['base_url']}/image-urls/static-images/mandators/{cosy['mandator']}"
        f"/collections/{_OPTIONS}/brands/{brand}/models/{spec.model}/options?{query}"
    )

    async def first_real(code: str, links: Sequence[str]) -> Optional[tuple[str, bytes]]:
        for link in links:
            try:
                data = await image(link)
            except ConfiguratorError:
                data = None
            if data:  # anything but a 200 is BMW's logo placeholder
                return code, data
        return None

    try:
        status, body = await _get(session, url, headers=cosy["headers"])
        if status != 200:
            _LOGGER.debug("Configurator: option pictures for %s answered %s", spec.model, status)
            return {}
        candidates = option_image_urls(json.loads(body))
        found = await asyncio.gather(
            *(first_real(code, candidates[code]) for code in spec.options if code in candidates)
        )
        pictures = [picture for picture in found if picture]
        if not pictures:
            return {}
        sheet, codes = await run(build_option_sheet, pictures)
    except (ConfiguratorError, ValueError) as err:
        _LOGGER.debug("Configurator: no option pictures for %s: %s", spec.model, err)
        return {}
    return {"options": sheet, "option_codes": tuple(codes)}
