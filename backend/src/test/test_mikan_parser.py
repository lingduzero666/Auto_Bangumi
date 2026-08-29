"""Tests for the Mikan homepage parser (cache + HTML extraction)."""

import datetime
import importlib
from unittest.mock import AsyncMock

import pytest

# `module.parser.analyser.__init__` re-exports the `mikan_parser` function under
# the same name as this submodule, shadowing the submodule on the package
# object — so `import module.parser.analyser.mikan_parser as x` would resolve
# to the function, not the module. Go through importlib to get the module.
mikan_parser_module = importlib.import_module("module.parser.analyser.mikan_parser")
MikanInfo = mikan_parser_module.MikanInfo
mikan_parser = mikan_parser_module.mikan_parser

_HOMEPAGE = "https://mikanani.me/Home/Episode/c89b3c6f0c1c0567a618f5288b853823"


_POSTER_DIV = """
      <div class="bangumi-poster"
           style="background-image: url('/images/Bangumi/202604/abc.jpg?width=400');">
      </div>
"""


def _page(air_date_line: str = "放送开始：4/3/2026") -> str:
    return f"""
    <html><body>
      {_POSTER_DIV}
      <p class="bangumi-title">
        <a href="/Home/Bangumi/3600#583">葬送的芙莉莲 第二季</a>
      </p>
      <p class="bangumi-info">{air_date_line}</p>
    </body></html>
    """


@pytest.fixture(autouse=True)
def _clear_cache():
    """conftest 没有清理这些模块级缓存的 autouse fixture，测试要自己来。"""
    mikan_parser_module.reset_cache()
    yield
    mikan_parser_module.reset_cache()


@pytest.fixture
def mikan_page(mocker):
    """Patch the network + image-saving seams and return a page setter."""

    def _install(html: str):
        async def fake_get_html(url: str) -> str:
            return html

        async def fake_get_content(url: str) -> bytes:
            return b"fake-image-bytes"

        mocker.patch.object(
            mikan_parser_module.RequestContent, "get_html", side_effect=fake_get_html
        )
        mocker.patch.object(
            mikan_parser_module.RequestContent,
            "get_content",
            side_effect=fake_get_content,
        )
        mocker.patch.object(
            mikan_parser_module,
            "save_image",
            new=AsyncMock(return_value="posters/abc.jpg"),
        )

    return _install


def test_reset_cache_clears_mikan_cache():
    """reset_cache() must drop all cached homepage lookups (called on config
    reload so a changed endpoint stops serving stale results)."""
    stale = "https://mikanani.me/Home/Episode/stale"
    mikan_parser_module._mikan_cache[stale] = MikanInfo("", "Stale Title")
    assert len(mikan_parser_module._mikan_cache) > 0

    mikan_parser_module.reset_cache()

    assert len(mikan_parser_module._mikan_cache) == 0


async def test_parses_title_poster_and_air_date(mikan_page):
    """Mikan 的日期是 en-US 的 M/D/YYYY：4/3/2026 是 2026 年 4 月 3 日。"""
    mikan_page(_page())

    info = await mikan_parser(_HOMEPAGE)

    assert info.official_title == "葬送的芙莉莲"  # 「第.*季」被剥掉
    assert info.poster_link == "posters/abc.jpg"
    assert info.air_date == datetime.date(2026, 4, 3)


async def test_air_date_accepts_fullwidth_and_halfwidth_colon(mikan_page):
    mikan_page(_page("放送开始: 12/25/2025"))

    info = await mikan_parser(_HOMEPAGE)

    assert info.air_date == datetime.date(2025, 12, 25)


async def test_missing_air_date_line_is_not_an_error(mikan_page):
    """日期是可选增强：缺了只把 air_date 置空，标题和海报照常返回。"""
    mikan_page(_page("首播时间未定"))

    info = await mikan_parser(_HOMEPAGE)

    assert info.air_date is None
    assert info.official_title == "葬送的芙莉莲"
    assert info.poster_link == "posters/abc.jpg"


async def test_invalid_air_date_is_ignored(mikan_page):
    """13 月 45 日这类非法日期必须返回 None，绝不能抛异常——
    调用方的 except AttributeError 语义是「页面结构变了」。"""
    mikan_page(_page("放送开始：13/45/2026"))

    info = await mikan_parser(_HOMEPAGE)

    assert info.air_date is None
    assert info.official_title == "葬送的芙莉莲"


async def test_broken_page_still_raises_attribute_error(mikan_page):
    """海报/标题元素缺失时仍必须抛 AttributeError（rss/analyser.py 依赖它
    区分「抓取或解析失败」与「正常无数据」）。"""
    mikan_page("<html><body><p>放送开始：4/3/2026</p></body></html>")

    with pytest.raises(AttributeError):
        await mikan_parser(_HOMEPAGE)


async def test_result_is_cached_by_homepage(mikan_page):
    mikan_page(_page())

    first = await mikan_parser(_HOMEPAGE)
    second = await mikan_parser(_HOMEPAGE)

    assert first is second
    assert mikan_parser_module._mikan_cache[_HOMEPAGE].air_date == datetime.date(
        2026, 4, 3
    )
