"""Tests for the mix source parser's degradation chain.

mix 组合 mikan（官方名 + 放送开始日期）与 tmdb（年份 + 季号），每一环拿不到
都要逐级降级，且绝不比单用 mikan 或 tmdb 更差。
"""

import dataclasses
import datetime
import importlib
from unittest.mock import AsyncMock

import pytest

from module.parser.analyser.mikan_parser import MikanInfo
from module.parser.analyser.tmdb_parser import TMDBInfo

# `module.parser.analyser.__init__` 用同名函数遮蔽了子模块，要 patch 模块里的
# 协作者只能走 importlib 拿模块对象（`import ... as x` 会拿到函数）。
mix_parser_module = importlib.import_module("module.parser.analyser.mix_parser")
mix_parser = mix_parser_module.mix_parser

_HOMEPAGE = "https://mikanani.me/Home/Episode/abc"
_AIR_DATE = datetime.date(2026, 4, 3)
_FALLBACK = "标题解析器给的名字"


def _tmdb(**overrides) -> TMDBInfo:
    base = TMDBInfo(
        id=1,
        title="TMDB 官方名",
        original_title="Original",
        season=[],
        last_season=3,
        year="2026",
        poster_link="tmdb-poster.jpg",
    )
    return dataclasses.replace(base, **overrides)


@pytest.fixture
def seams(mocker):
    """Patch mix 的两个协作者，返回 (mikan_mock, tmdb_mock)。"""
    mikan = AsyncMock()
    tmdb = AsyncMock(return_value=_tmdb())
    mocker.patch.object(mix_parser_module, "mikan_parser", new=mikan)
    mocker.patch.object(mix_parser_module, "tmdb_parser", new=tmdb)
    return mikan, tmdb


async def test_without_homepage_degrades_to_plain_tmdb(seams):
    """非 Mikan 源没有 homepage：用标题解析器的名字查 TMDB，等同纯 tmdb。"""
    mikan, tmdb = seams

    result = await mix_parser(None, _FALLBACK, "zh")

    mikan.assert_not_awaited()
    tmdb.assert_awaited_once_with(_FALLBACK, "zh", is_movie=False, air_date=None)
    assert result.official_title == "TMDB 官方名"


async def test_mikan_failure_degrades_to_plain_tmdb(seams):
    """mikan_parser 用 AttributeError 表示抓取/解析失败，mix 要接住并降级。"""
    mikan, tmdb = seams
    mikan.side_effect = AttributeError("'NoneType' object has no attribute 'get'")

    result = await mix_parser(_HOMEPAGE, _FALLBACK, "zh")

    tmdb.assert_awaited_once_with(_FALLBACK, "zh", is_movie=False, air_date=None)
    assert result.official_title == "TMDB 官方名"


async def test_mikan_without_air_date_still_improves_the_query(seams):
    """拿不到放送日期时仍用 Mikan 的官方名当搜索词——这本身就比种子名准。"""
    mikan, tmdb = seams
    mikan.return_value = MikanInfo("mikan-poster.jpg", "Mikan 官方名", None)

    await mix_parser(_HOMEPAGE, _FALLBACK, "zh")

    tmdb.assert_awaited_once_with("Mikan 官方名", "zh", is_movie=False, air_date=None)


async def test_air_date_is_forwarded_and_matched_season_wins(seams):
    mikan, tmdb = seams
    mikan.return_value = MikanInfo("mikan-poster.jpg", "Mikan 官方名", _AIR_DATE)
    tmdb.return_value = _tmdb(matched_season=2, last_season=3)

    result = await mix_parser(_HOMEPAGE, _FALLBACK, "zh")

    tmdb.assert_awaited_once_with(
        "Mikan 官方名", "zh", is_movie=False, air_date=_AIR_DATE
    )
    assert result.official_title == "TMDB 官方名"
    assert result.year == "2026"
    assert result.season == 2  # 匹配到的季，而不是总季数
    assert result.poster_link == "tmdb-poster.jpg"


async def test_season_falls_back_to_last_season(seams):
    """没有日期匹配结果时沿用 TMDB 的季数，与既有 tmdb 解析器一致。"""
    mikan, tmdb = seams
    mikan.return_value = MikanInfo("mikan-poster.jpg", "Mikan 官方名", None)
    tmdb.return_value = _tmdb(matched_season=None, last_season=3)

    result = await mix_parser(_HOMEPAGE, _FALLBACK, "zh")

    assert result.season == 3


async def test_tmdb_miss_keeps_mikan_data(seams):
    """TMDB 未命中（含日期全部超出选番窗口）：退回 Mikan 的名字与海报，
    季号和年份留空表示「不要覆盖调用方已有的值」。"""
    mikan, tmdb = seams
    mikan.return_value = MikanInfo("mikan-poster.jpg", "Mikan 官方名", _AIR_DATE)
    tmdb.return_value = None

    result = await mix_parser(_HOMEPAGE, _FALLBACK, "zh")

    assert result.official_title == "Mikan 官方名"
    assert result.poster_link == "mikan-poster.jpg"
    assert result.season is None
    assert result.year is None


async def test_poster_prefers_tmdb_but_falls_back_to_mikan(seams):
    mikan, tmdb = seams
    mikan.return_value = MikanInfo("mikan-poster.jpg", "Mikan 官方名", _AIR_DATE)
    tmdb.return_value = _tmdb(poster_link=None)

    result = await mix_parser(_HOMEPAGE, _FALLBACK, "zh")

    assert result.poster_link == "mikan-poster.jpg"


async def test_movie_forwards_is_movie_and_never_returns_a_season(seams):
    mikan, tmdb = seams
    mikan.return_value = MikanInfo("mikan-poster.jpg", "剧场版名", _AIR_DATE)
    tmdb.return_value = _tmdb(last_season=0, matched_season=None)

    result = await mix_parser(_HOMEPAGE, _FALLBACK, "zh", is_movie=True)

    tmdb.assert_awaited_once_with("剧场版名", "zh", is_movie=True, air_date=_AIR_DATE)
    assert result.season is None


async def test_no_usable_title_returns_empty_result(seams):
    """Mikan 和标题解析器都没给出名字：不发无意义的 TMDB 请求。"""
    mikan, tmdb = seams
    mikan.return_value = MikanInfo("", "", None)

    result = await mix_parser(_HOMEPAGE, "", "zh")

    tmdb.assert_not_awaited()
    assert result.official_title is None
    assert result.poster_link is None


# ---------------------------------------------------------------------------
# RSSAnalyser 里的接线（parser == "mix" 的分支）
# ---------------------------------------------------------------------------


async def test_analyser_mix_branch_writes_bangumi_fields(mocker):
    from module.rss.analyser import RSSAnalyser
    from test.factories import make_bangumi, make_rss_item, make_torrent

    analyser_module = importlib.import_module("module.rss.analyser")
    mocker.patch.object(
        analyser_module,
        "mix_parser",
        new=AsyncMock(
            return_value=mix_parser_module.MixResult(
                official_title="TMDB 官方名",
                year="2026",
                season=2,
                poster_link="tmdb-poster.jpg",
            )
        ),
    )
    bangumi = make_bangumi(official_title="种子名里的名字", season=1, year="2024")

    await RSSAnalyser().official_title_parser(
        bangumi=bangumi,
        rss=make_rss_item(parser="mix"),
        torrent=make_torrent(homepage=_HOMEPAGE),
    )

    assert bangumi.official_title == "TMDB 官方名"
    assert bangumi.year == "2026"
    assert bangumi.season == 2
    assert bangumi.poster_link == "tmdb-poster.jpg"


async def test_analyser_mix_branch_keeps_existing_values_when_empty(mocker):
    """MixResult 的空字段表示「没拿到」，不能把已有值清掉。"""
    from module.rss.analyser import RSSAnalyser
    from test.factories import make_bangumi, make_rss_item, make_torrent

    analyser_module = importlib.import_module("module.rss.analyser")
    mocker.patch.object(
        analyser_module,
        "mix_parser",
        new=AsyncMock(return_value=mix_parser_module.MixResult()),
    )
    bangumi = make_bangumi(official_title="原名", season=1, year="2024")

    await RSSAnalyser().official_title_parser(
        bangumi=bangumi,
        rss=make_rss_item(parser="mix"),
        torrent=make_torrent(homepage=None),
    )

    assert bangumi.official_title == "原名"
    assert bangumi.year == "2024"
    assert bangumi.season == 1


async def test_analyser_mix_branch_is_skipped_when_not_fetching_poster(mocker):
    """搜索预览走 fetch_poster=False，不该为每条结果发两轮网络请求。"""
    from module.rss.analyser import RSSAnalyser
    from test.factories import make_bangumi, make_rss_item, make_torrent

    analyser_module = importlib.import_module("module.rss.analyser")
    spy = AsyncMock()
    mocker.patch.object(analyser_module, "mix_parser", new=spy)

    await RSSAnalyser().official_title_parser(
        bangumi=make_bangumi(),
        rss=make_rss_item(parser="mix"),
        torrent=make_torrent(homepage=_HOMEPAGE),
        fetch_poster=False,
    )

    spy.assert_not_awaited()
