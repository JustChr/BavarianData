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
            brand = re.search(r"/brands/([^/]+)/", url).group(1)
            images = [{"viewImage": f"https://cosy.test/img/{brand}/{i}"} for i in range(36)]
            body = {"walkaround-360": {"mobile": {"images": images}}}
            return _Resp(200, json.dumps(body).encode())
        if "/img/" in url:
            brand, index = url.rsplit("/", 2)[-2:]
            if brand != self.draws:
                return _Resp(404, b"placeholder png")
            return _Resp(200, _frame(200 + int(index)))
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


def test_a_refused_key_is_unavailable():
    with pytest.raises(CONF.ConfiguratorError) as err:
        asyncio.run(CONF.async_render(_FakeBmw(key_ok=False), I5))
    assert err.value.reason == "unavailable"
