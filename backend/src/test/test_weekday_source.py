"""Tests for the parser-side air-weekday source.

``parser_weekday`` 按订阅自己的 parser 分派，并且**绝不抛异常**：调用方是日历
的批量刷新，一部番剧的页面变动或网络故障不该中断整批。
"""

import importlib
from unittest.mock import AsyncMock, patch

import pytest

weekday_source = importlib.import_module("module.parser.analyser.weekday_source")
parser_weekday = weekday_source.parser_weekday

HOMEPAGE = "https://mikanani.me/Home/Episode/abc"


@pytest.fixture
def mikan():
    with patch.object(
        weekday_source, "mikan_weekday", new_callable=AsyncMock, return_value=5
    ) as m:
        yield m


@pytest.fixture
def tmdb():
    with patch.object(weekday_source, "tmdb_parser", new_callable=AsyncMock) as m:
        m.return_value = type("Info", (), {"air_weekday": 3})()
        yield m


@pytest.mark.parametrize("parser", ["mikan", "mix"])
async def test_mikan_parsers_read_the_bangumi_page(mikan, tmdb, parser):
    """mix 内部也是先抓 Mikan 主页，所以与 mikan 同路。"""
    result = await parser_weekday(
        parser=parser, official_title="A", episode_homepage=HOMEPAGE
    )

    assert result == 5
    mikan.assert_awaited_once_with(HOMEPAGE)
    tmdb.assert_not_awaited()


async def test_tmdb_parser_derives_from_air_dates(mikan, tmdb):
    result = await parser_weekday(parser="tmdb", official_title="A")

    assert result == 3
    mikan.assert_not_awaited()


async def test_mikan_without_a_homepage_gives_up(mikan, tmdb):
    """非 Mikan 源、老数据、从未下载过的番剧都没有 homepage。这是常见路径，
    不是异常：直接判无解，交给 bgm 兜底，而不是改问 TMDB。"""
    result = await parser_weekday(parser="mikan", official_title="A")

    assert result is None
    mikan.assert_not_awaited()
    tmdb.assert_not_awaited()


@pytest.mark.parametrize("parser", ["parser", "unknown", None])
async def test_other_parsers_have_no_source(mikan, tmdb, parser):
    result = await parser_weekday(
        parser=parser, official_title="A", episode_homepage=HOMEPAGE
    )

    assert result is None
    mikan.assert_not_awaited()
    tmdb.assert_not_awaited()


async def test_movies_are_skipped(mikan, tmdb):
    """剧场版没有每周档期。"""
    result = await parser_weekday(
        parser="mikan",
        official_title="A",
        episode_type="movie",
        episode_homepage=HOMEPAGE,
    )

    assert result is None
    mikan.assert_not_awaited()


async def test_tmdb_miss_returns_none(mikan, tmdb):
    tmdb.return_value = None

    assert await parser_weekday(parser="tmdb", official_title="A") is None


async def test_empty_title_skips_tmdb(mikan, tmdb):
    assert await parser_weekday(parser="tmdb", official_title="") is None
    tmdb.assert_not_awaited()


@pytest.mark.parametrize("parser", ["mikan", "tmdb"])
async def test_failures_degrade_instead_of_raising(mikan, tmdb, parser):
    """核心契约：批量刷新里单条失败必须降级为 None。"""
    mikan.side_effect = RuntimeError("boom")
    tmdb.side_effect = RuntimeError("boom")

    result = await parser_weekday(
        parser=parser, official_title="A", episode_homepage=HOMEPAGE
    )

    assert result is None
