"""Pictures from BMW's configurator: the request, the fallbacks, the sprite.

No network: a tiny fake session answers the configurator page, its settings
file, the image service and the frames the way BMW's servers do, including the
wrong-brand trap (links that all return a placeholder with HTTP 404).
"""

from __future__ import annotations

import asyncio
import io
import json
import re

import pytest
from urllib.parse import parse_qs, urlsplit

from PIL import Image

from .conftest import load_module

CONF = load_module("configurator")

I5 = {
    "brand": "BMW",
    "modelKey": "71HH",
    "colourCodeRaw": "C4P",
    "constructionDate": "2025-09-26T00:00:00.000+0000",
    "fullSAList": "0230,0248,02PA,0337",
}


def test_spec_from_basic_data_builds_configurator_codes():
    spec = CONF.spec_from_basic_data(I5)
    assert spec.site == "bmw"
    assert spec.brands == ("bmwCar", "bmwi")
    assert spec.model == "71HH"
    assert spec.options == ("P0C4P", "S0230", "S0248", "S02PA", "S0337")
    assert spec.effect_date == "20250926"


def test_bmw_i_and_mini_pick_their_brand_order_and_site():
    assert CONF.spec_from_basic_data({**I5, "brand": "BMWi"}).brands == ("bmwi", "bmwCar")
    mini = CONF.spec_from_basic_data({**I5, "brand": "MINI"})
    assert (mini.site, mini.brands) == ("mini", ("mini",))


def test_junk_codes_are_dropped_not_sent():
    spec = CONF.spec_from_basic_data(
        {**I5, "colourCodeRaw": "", "fullSAList": " 0337 ,,x,TOOLONG,02pa"}
    )
    assert spec.options == ("S0337", "S02PA")


def test_a_code_the_car_lists_twice_is_sent_once_in_the_cars_order():
    spec = CONF.spec_from_basic_data({**I5, "fullSAList": "0337,0258,0337,08S4"})
    assert spec.options == ("P0C4P", "S0337", "S0258", "S08S4")
    assert spec.sorted_options == "P0C4P,S0258,S0337,S08S4"


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        ({**I5, "brand": "BMW Motorrad"}, "unsupported"),
        ({**I5, "brand": "Rolls-Royce"}, "unsupported"),
        ({**I5, "brand": None}, "unsupported"),
        ({**I5, "modelKey": None}, "no_data"),
        ({**I5, "constructionDate": ""}, "no_data"),
    ],
)
def test_cars_the_configurator_cannot_draw_say_why(raw, reason):
    with pytest.raises(CONF.ConfiguratorError) as err:
        CONF.spec_from_basic_data(raw)
    assert err.value.reason == reason


def test_settings_file_is_found_in_the_page():
    html = "<link rel=preload href=`${base}/de_DE/settings.bb566ca8.json`>"
    assert CONF.settings_path(html) == "settings.bb566ca8.json"
    assert CONF.settings_path("<html>nothing here</html>") is None


def test_a_changed_settings_file_is_unavailable_not_a_crash():
    with pytest.raises(CONF.ConfiguratorError) as err:
        CONF.cosy_settings({"APP": {"backend": {}}})
    assert err.value.reason == "unavailable"


def _frame(x: int, color=(200, 30, 40)) -> bytes:
    """A 960x540 transparent frame with a 300x100 'car' whose left edge is ``x``."""

    image = Image.new("RGBA", (960, 540), (0, 0, 0, 0))
    image.paste(Image.new("RGBA", (300, 100), (*color, 255)), (x, 300))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _on_studio(frame: bytes, studio=(16, 16, 16)) -> bytes:
    """``frame`` flattened onto BMW's opaque night studio."""

    car = Image.open(io.BytesIO(frame)).convert("RGBA")
    out = Image.new("RGBA", car.size, (*studio, 255))
    out.alpha_composite(car)
    data = io.BytesIO()
    out.save(data, "PNG")
    return data.getvalue()


def test_sprite_crops_one_box_for_the_whole_spin_and_samples_the_paint():
    frames = [_frame(200 + 4 * i) for i in range(36)]
    render = CONF.build_sprite(frames)
    assert (render.columns, render.rows, render.frames) == (6, 6, 36)
    sprite = Image.open(io.BytesIO(render.sprite))
    assert sprite.size == (6 * 640, 6 * 360)
    assert sprite.mode == "RGBA"
    # The car fills the frame once cropped, and the corners stay transparent.
    first = sprite.crop((0, 0, 640, 360))
    box = first.getchannel("A").getbbox()
    assert box[2] - box[0] > 640 * 0.5
    assert first.getpixel((2, 2))[3] == 0
    assert render.paint == "#c81e28"
    assert Image.open(io.BytesIO(render.still)).mode == "RGBA"


def test_paint_is_none_without_a_car_in_the_frame():
    empty = Image.new("RGBA", (960, 540), (0, 0, 0, 0))
    assert CONF.paint_color(empty) is None


class _Resp:
    def __init__(self, status: int, body: bytes) -> None:
        self.status, self._body = status, body

    async def read(self) -> bytes:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeBmw:
    """Answers like configure.bmw.de and prod.cosy.bmw.cloud do."""

    def __init__(self, draws: str = "bmwi", key_ok: bool = True) -> None:
        self.draws, self.key_ok, self.calls = draws, key_ok, []

    def get(self, url: str, **kwargs):
        self.calls.append(url)
        if url.endswith("/de_DE/model-ranges"):
            return _Resp(200, b"... /de_DE/settings.abc123.json ...")
        if url.endswith("/de_DE/settings.abc123.json"):
            cosy = {
                "baseUrl": "https://cosy.test",
                "mandator": "connext-bmw",
                "hub": "HUB",
                "headers": {"x-api-key": "k"},
            }
            return _Resp(200, json.dumps({"APP": {"backend": {"settingsCosy": cosy}}}).encode())
        if "/image-urls/" in url:
            if not self.key_ok:
                return _Resp(403, b"{}")
            options = parse_qs(urlsplit(url).query).get("options", [""])[0].split(",")
            if options != sorted(set(options)):
                refused = {
                    "errorCode": "INVALID_PARAMETER_VALUE",
                    "errorMessage": "options is not sorted",
                }
                return _Resp(400, json.dumps(refused).encode())
            brand = re.search(r"/brands/([^/]+)/", url).group(1)
            # Both night scenes come back on an opaque studio, not transparent.
            scene = "night" if "nightview=true" in url or "background=night" in url else "img"
            images = [{"viewImage": f"https://cosy.test/{scene}/{brand}/{i}"} for i in range(36)]
            body = {"walkaround-360": {"mobile": {"images": images}}}
            return _Resp(200, json.dumps(body).encode())
        if "/img/" in url or "/night/" in url:
            brand, index = url.rsplit("/", 2)[-2:]
            if brand != self.draws:
                return _Resp(404, b"placeholder png")
            frame = _frame(200 + int(index))
            if "/night/" in url:
                frame = _on_studio(frame)
            return _Resp(200, frame)
        return _Resp(404, b"")


def test_render_falls_back_to_the_other_brand_name_like_the_configurator():
    session = _FakeBmw(draws="bmwi")
    render = asyncio.run(CONF.async_render(session, I5))
    assert render.brand == "bmwi"
    assert render.frames == 36
    # The placeholder brand cost one probe, not 36 downloads.
    assert sum("/img/bmwCar/" in c for c in session.calls) == 1
    image_call = next(c for c in session.calls if "/image-urls/" in c)
    assert "background=TRANSPARENT" in image_call
    assert "effectdate=20250926" in image_call
    assert "options=P0C4P%2CS0230" in image_call


def test_render_without_any_drawable_brand_is_no_render():
    with pytest.raises(CONF.ConfiguratorError) as err:
        asyncio.run(CONF.async_render(_FakeBmw(draws="nobody"), I5))
    assert err.value.reason == "no_render"


def test_a_car_listing_its_options_out_of_order_still_draws():
    # Issue #130: an F40 118i reports its codes unsorted; BMW refuses that.
    session = _FakeBmw(draws="bmwCar")
    raw = {**I5, "fullSAList": "0710,0258,0337,0258"}
    render = asyncio.run(CONF.async_render(session, raw))
    assert render.brand == "bmwCar"
    image_call = next(c for c in session.calls if "/image-urls/" in c)
    assert "options=P0C4P%2CS0258%2CS0337%2CS0710&" in image_call


def test_a_refused_request_says_why_and_skips_the_other_brand(monkeypatch):
    session = _FakeBmw(draws="bmwi")
    monkeypatch.setattr(CONF.CarSpec, "sorted_options", property(lambda spec: "S0337,S0258"))
    with pytest.raises(CONF.ConfiguratorError) as err:
        asyncio.run(CONF.async_render(session, I5))
    assert err.value.reason == "no_render"
    assert "options is not sorted" in str(err.value)
    assert sum("/image-urls/" in c for c in session.calls) == 1


def test_a_refused_key_is_unavailable():
    with pytest.raises(CONF.ConfiguratorError) as err:
        asyncio.run(CONF.async_render(_FakeBmw(key_ok=False), I5))
    assert err.value.reason == "unavailable"


# --- Everything beyond the plain spin ----------------------------------------

NAMES = {
    "P0C4P": {
        "optionType": "paint",
        "displayTypeId": "1",
        "phrases": {"longDescription": "M Brooklyn Grey metallic"},
    },
    "S0230": {
        "optionType": "other",
        "displayTypeId": "2",
        "phrases": {"longDescription": "EU-specific  equipment"},
    },
    "S0337": {
        "optionType": "package",
        "displayTypeId": "1",
        "phrases": {"longDescription": "M Sport package"},
    },
    "S02PA": {"optionType": "other", "phrases": {}},
}


def test_equipment_keeps_the_cars_order_and_marks_the_technical_codes():
    spec = CONF.spec_from_basic_data(I5)
    items = CONF.equipment_list(NAMES, spec)
    assert [i["code"] for i in items] == ["P0C4P", "S0230", "S0337"]  # S0248 unknown, S02PA unnamed
    assert items[0] == {
        "code": "P0C4P",
        "name": "M Brooklyn Grey metallic",
        "kind": "paint",
        "shown": True,
    }
    assert items[1]["shown"] is False  # displayTypeId 2: BMW's own "technical"
    assert items[1]["name"] == "EU-specific equipment"  # whitespace tidied


def test_interior_faces_are_laid_out_three_by_two():
    faces = []
    for shade in range(6):
        out = io.BytesIO()
        Image.new("RGB", (2000, 2000), (shade * 40, 0, 0)).save(out, "PNG")
        faces.append(out.getvalue())
    sheet = Image.open(io.BytesIO(CONF.build_interior(faces)))
    assert sheet.size == (3 * CONF.FACE_SIZE, 2 * CONF.FACE_SIZE)
    # Face 5 (displayOrder 6) sits bottom right.
    assert abs(sheet.getpixel((int(2.5 * CONF.FACE_SIZE), int(1.5 * CONF.FACE_SIZE)))[0] - 200) < 6
    with pytest.raises(CONF.ConfiguratorError):
        CONF.build_interior(faces[:5])


def test_the_top_view_is_cut_out_and_turned_nose_up():
    # BMW's MINI top view: nose left on a white page, with a white roof stripe
    # inside the car that must stay opaque.
    page = Image.new("RGB", (1920, 1000), (255, 255, 255))
    page.paste(Image.new("RGB", (800, 360), (30, 30, 40)), (500, 320))
    page.paste(Image.new("RGB", (300, 40), (255, 255, 255)), (700, 480))
    page.paste(Image.new("RGB", (40, 360), (200, 0, 0)), (500, 320))  # the nose, red
    out = io.BytesIO()
    page.save(out, "PNG")
    top = Image.open(io.BytesIO(CONF.build_top(out.getvalue()))).convert("RGBA")
    assert top.size == (360, 800)  # cropped to the car, turned upright
    assert top.getpixel((180, 5))[:3] == (200, 0, 0)  # nose at the top
    assert top.getpixel((180, 400))[3] == 255  # the roof stripe kept
    padded = Image.new("RGBA", (top.width + 2, top.height + 2), (0, 0, 0, 0))
    padded.paste(top, (1, 1))
    assert padded.getpixel((0, 0))[3] == 0


def _tile(color=(20, 60, 160), size=(168, 95)) -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", size, (*color, 255)).save(out, "PNG")
    return out.getvalue()


class _Timeout:
    async def __aenter__(self):
        raise asyncio.TimeoutError

    async def __aexit__(self, *exc):
        return False


def _option_links(*codes: str) -> dict:
    """The option-picture answer: two links per code, as BMW sends them."""

    def group(view, code):
        return {
            "distinctionParams": {"VIEW": view},
            "images": [{"viewImage": f"https://cosy.test/opt/{code}/{view}", "imageNumber": 1}],
        }

    block = {
        code: {"imageGroups": [group("tile_wide", code), group("static_wide", code)]}
        for code in codes
    }
    return {"collections": {"unified-sgt-options": {"mobile": block, "desktop": block}}}


# Which link of each code is real: paint is a swatch, packages are photos,
# S0230 has nothing, and S0248's only real picture never answers.
_OPTION_PICTURES = {
    "P0C4P/tile_wide": _tile((90, 90, 100)),
    "S0337/static_wide": _tile((200, 20, 20)),
}


class _FakeBmwFull(_FakeBmw):
    """Also answers the interior, the top view, the option names and pictures."""

    def __init__(self, **kwargs) -> None:
        super().__init__(draws="bmwCar", **kwargs)
        self.name_calls = []
        self.option_calls = []

    def get(self, url: str, **kwargs):
        if url.endswith("/de_DE/settings.abc123.json"):
            backend = {
                "settingsCosy": {
                    "baseUrl": "https://cosy.test",
                    "mandator": "connext-bmw",
                    "hub": "HUB",
                    "headers": {"x-api-key": "k"},
                },
                "settingsLocalisation": {
                    "baseUrl": "https://ucp.test",
                    "source": "pcaso",
                    "brand": "bmwCar",
                    "application": "connext",
                    "headers": {"x-api-key": "n"},
                },
            }
            return _Resp(200, json.dumps({"APP": {"backend": backend}}).encode())
        if "/localisations/" in url:
            self.name_calls.append(url)
            market, lang = re.search(r"/countries/([^/]+)/.*/languages/([^/]+)$", url).groups()
            # A German-market car: Austria answers in German only.
            if market == "at" and lang == "de":
                return _Resp(200, json.dumps(NAMES).encode())
            return _Resp(404, b"{}")
        if "/collections/interior-mixed/" in url:
            faces = [
                {"viewImage": f"https://cosy.test/face/{i}", "displayOrder": 6 - i}
                for i in range(6)
            ]
            return _Resp(
                200, json.dumps({"interior-mixed": {"res_2000x2000": {"images": faces}}}).encode()
            )
        if "/collections/roof-and-mirror-caps/" in url:
            images = [
                {"viewImage": "https://cosy.test/top", "distinctionParameters": {"POV": "TOP"}}
            ]
            body = {
                "roof-and-mirror-caps": {
                    "desktop": {"namedImages": [{"name": "POVS", "images": images}]}
                }
            }
            return _Resp(200, json.dumps(body).encode())
        if url.startswith("https://cosy.test/face/"):
            out = io.BytesIO()
            Image.new("RGB", (100, 100), (90, 90, 90)).save(out, "PNG")
            return _Resp(200, out.getvalue())
        if url == "https://cosy.test/top":
            return _Resp(404, b"placeholder")  # what every BMW gets here
        if "/collections/unified-sgt-options/" in url:
            self.option_calls.append((url, kwargs.get("headers")))
            codes = re.search(r"options=([^&]+)", url).group(1).split("%2C")
            if codes != sorted(codes):
                return _Resp(400, b'{"errorMessage": "options is not sorted"}')
            return _Resp(200, json.dumps(_option_links(*codes)).encode())
        if url.startswith("https://cosy.test/opt/"):
            name = url.removeprefix("https://cosy.test/opt/")
            if name == "S0248/static_wide":
                return _Timeout()
            if name in _OPTION_PICTURES:
                return _Resp(200, _OPTION_PICTURES[name])
            return _Resp(404, _tile((0, 0, 0)))  # the brand logo, with a 404
        return super().get(url, **kwargs)


def test_the_full_fetch_adds_lights_night_interior_and_names():
    session = _FakeBmwFull()
    render = asyncio.run(CONF.async_render(session, {**I5, "countryCode": "AT"}, languages=("en",)))
    assert render.lights and render.night and render.interior
    assert render.top is None  # BMW's placeholder for a BMW
    assert [e["code"] for e in render.equipment] == ["P0C4P", "S0230", "S0337"]
    assert render.equipment_language == "de"
    # Asked in the user's language first, then the market's own.
    assert [c.rsplit("/", 1)[1] for c in session.name_calls] == ["en", "de"]
    spins = [c for c in session.calls if "/collections/walkaround-360/" in c]
    assert any("lightson=true" in c for c in spins) and any("nightview=true" in c for c in spins)


def test_the_night_spin_is_cut_out_so_the_card_draws_the_backdrop():
    render = asyncio.run(CONF.async_render(_FakeBmwFull(), I5))
    night = Image.open(io.BytesIO(render.night)).convert("RGBA")
    day = Image.open(io.BytesIO(render.sprite)).convert("RGBA")
    assert night.size == day.size
    # BMW's black studio is gone where the plain spin has no car...
    assert night.getpixel((2, 2))[3] == 0
    # ...and the car itself stays, frame for frame.
    box = day.crop((0, 0, 640, 360)).getchannel("A").getbbox()
    middle = ((box[0] + box[2]) // 2, (box[1] + box[3]) // 2)
    assert night.getpixel(middle)[3] == 255


def test_a_mini_is_asked_for_its_own_night_scene():
    class FakeMini(_FakeBmwFull):
        def __init__(self) -> None:
            super().__init__()
            self.draws = "mini"

    session = FakeMini()
    render = asyncio.run(CONF.async_render(session, {**I5, "brand": "MINI"}))
    assert render.night
    night_calls = [c for c in session.calls if "/walkaround-360/" in c and "night" in c]
    # MINI's configurator lights the car in its night scene; nightview would
    # leave it a dark shadow.
    assert night_calls and all(
        "background=night" in c and "lightson=true" in c and "nightview" not in c
        for c in night_calls
    )


def test_a_stencil_without_transparency_leaves_the_night_spin_alone():
    def sprite(rgba):
        out = io.BytesIO()
        Image.new("RGBA", (64, 36), rgba).save(out, "PNG")
        return out.getvalue()

    night = sprite((16, 16, 16, 255))
    assert CONF.cut_out(night, sprite((255, 255, 255, 255))) == night


def test_the_full_fetch_keeps_only_real_option_pictures():
    session = _FakeBmwFull()
    render = asyncio.run(CONF.async_render(session, {**I5, "countryCode": "GB"}))
    # S0230's links are both placeholders and S0248's never answers: left out.
    assert render.option_codes == ("P0C4P", "S0337")
    sheet = Image.open(io.BytesIO(render.options)).convert("RGBA")
    assert sheet.size == (CONF.OPTION_COLUMNS * 168, 95)

    def near(pixel, rgb):  # WebP is lossy
        return all(abs(a - b) < 8 for a, b in zip(pixel[:3], rgb, strict=True))

    assert near(sheet.getpixel((10, 10)), (90, 90, 100))  # the paint swatch first
    assert near(sheet.getpixel((168 + 10, 10)), (200, 20, 20))
    assert sheet.getpixel((2 * 168 + 10, 10))[3] == 0  # no logo kept
    ((url, headers),) = session.option_calls
    assert "options=P0C4P%2CS0230%2CS0248%2CS02PA%2CS0337" in url  # sorted
    assert "params.market=gb" in url and "imagetype=webp" in url
    assert "/static-images/mandators/connext-bmw/" in url and "/models/71HH/options?" in url
    assert headers == {"x-api-key": "k"}


def test_missing_option_pictures_never_fail_the_render():
    class NoPictures(_FakeBmwFull):
        def get(self, url: str, **kwargs):
            if "/collections/unified-sgt-options/" in url:
                return _Resp(500, b"")
            return super().get(url, **kwargs)

    render = asyncio.run(CONF.async_render(NoPictures(), I5))
    assert render.frames == 36 and render.options is None and render.option_codes == ()


def test_a_bmw_swatch_is_read_from_its_square_tile():
    # BMW names the paint, wheel and trim swatch tile_square; MINI tile_wide.
    answer = {
        "collections": {
            "unified-sgt-options": {
                "desktop": {
                    "P0C4P": {
                        "imageGroups": [
                            {
                                "distinctionParams": {"VIEW": view},
                                "images": [{"viewImage": f"https://cosy.test/opt/P0C4P/{view}"}],
                            }
                            for view in ("tile_square", "static_wide")
                        ]
                    }
                }
            }
        }
    }
    assert CONF.option_image_urls(answer) == {
        "P0C4P": [
            "https://cosy.test/opt/P0C4P/static_wide",
            "https://cosy.test/opt/P0C4P/tile_square",
        ]
    }


def test_option_links_are_read_per_code_photo_first():
    links = CONF.option_image_urls(_option_links("S0337"))
    assert links == {
        "S0337": [
            "https://cosy.test/opt/S0337/static_wide",
            "https://cosy.test/opt/S0337/tile_wide",
        ]
    }
    for junk in (None, {}, {"collections": {}}, {"collections": {"unified-sgt-options": []}}):
        assert CONF.option_image_urls(junk) == {}
    broken = {
        "collections": {
            "unified-sgt-options": {"desktop": {"S1": None, "S2": {"imageGroups": [1]}}}
        }
    }
    assert CONF.option_image_urls(broken) == {}


def test_option_sheet_fits_odd_sizes_and_skips_unreadable_pictures():
    sheet, codes = CONF.build_option_sheet(
        [("A", _tile()), ("B", b"not an image"), ("C", _tile(size=(336, 95)))], columns=2
    )
    assert codes == ["A", "C"]
    image = Image.open(io.BytesIO(sheet)).convert("RGBA")
    assert image.size == (2 * 168, 95)
    assert image.getpixel((168 + 84, 2))[3] == 0  # halved to 168x47, centred
    assert image.getpixel((168 + 84, 47))[3] == 255
    with pytest.raises(CONF.ConfiguratorError):
        CONF.build_option_sheet([("B", b"junk")])


def test_the_quick_fetch_stops_after_the_plain_spin():
    session = _FakeBmwFull()
    render = asyncio.run(CONF.async_render(session, I5, extras=False))
    assert render.frames == 36 and render.lights is None and render.equipment == ()
    assert render.options is None and session.option_calls == []
    assert not any("lightson" in c or "interior" in c for c in session.calls)


def test_variant_spins_reuse_the_plain_spins_crop():
    frames = [_frame(200 + 4 * i) for i in range(36)]
    plain = CONF.build_sprite(frames)
    dark = [_frame(200 + 4 * i, color=(10, 10, 10)) for i in range(36)]
    assert CONF.build_sprite(dark, crop=plain.crop).crop == plain.crop
