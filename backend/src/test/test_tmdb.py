"""Tests for the TMDB parser.

The default (mocked) test never touches the network: it patches
``RequestContent.get_json`` with fixture data so the suite stays
deterministic and offline. A separate live test exercises the real TMDB API
and is skipped unless explicitly opted into.
"""

import datetime
import importlib
import os
import re
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest

from module.parser.analyser.tmdb_parser import tmdb_parser

# `module.parser.analyser.__init__` re-exports the `tmdb_parser` function under
# the same name as this submodule, shadowing the submodule on the package
# object — so `import module.parser.analyser.tmdb_parser as x` would resolve
# to the function, not the module. Go through importlib to get the module.
tmdb_parser_module = importlib.import_module("module.parser.analyser.tmdb_parser")


def _query_params(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


_SHOW_INFO = {
    "genres": [{"id": 16, "name": "Animation"}],
    "name": "冰海战记",
    "original_name": "ヴィンランド・サガ",
    "first_air_date": "2019-07-08",
    "status": "Ended",
    "poster_path": "/poster.jpg",
    "seasons": [
        {
            "name": "第 1 季",
            "air_date": "2019-07-08",
            "poster_path": "/s1.jpg",
            "season_number": 1,
            "episode_count": 24,
        },
        {
            "name": "第 2 季",
            "air_date": "2023-01-09",
            "poster_path": "/s2.jpg",
            "season_number": 2,
            "episode_count": 24,
        },
    ],
}


async def _fake_get_json(url: str) -> dict:
    if "/search/tv" in url:
        return {"results": [{"id": 82684}]}
    if "/season/" in url:
        return {"episodes": []}
    return _SHOW_INFO


async def test_tmdb_parser(mocker):
    mocker.patch.object(
        tmdb_parser_module.RequestContent, "get_json", side_effect=_fake_get_json
    )
    tmdb_parser_module._tmdb_cache.clear()

    bangumi_title = "海盗战记"
    bangumi_year = "2019"
    bangumi_season = 2

    tmdb_info = await tmdb_parser(bangumi_title, "zh", test=True)

    assert tmdb_info is not None
    assert tmdb_info.title == "冰海战记"
    assert tmdb_info.year == bangumi_year
    assert tmdb_info.last_season == bangumi_season


_MOVIE_SEARCH_RESULT = {
    "results": [
        {
            "id": 372058,
            "title": "你的名字。",
            "original_title": "君の名は。",
            "release_date": "2016-08-26",
            "poster_path": "/movie_poster.jpg",
        }
    ]
}


async def test_tmdb_parser_movie_fallback_when_tv_search_misses(mocker):
    """When search/tv has no results at all, fall back to search/movie
    (e.g. for a theatrical release with no matching TV series)."""

    search_requests: list[tuple[str, dict[str, list[str]]]] = []

    async def fake_get_json(url: str) -> dict:
        if "/search/tv" in url:
            search_requests.append(("tv", _query_params(url)))
            return {"results": []}
        if "/search/movie" in url:
            search_requests.append(("movie", _query_params(url)))
            return _MOVIE_SEARCH_RESULT
        return {}

    mocker.patch.object(
        tmdb_parser_module.RequestContent, "get_json", side_effect=fake_get_json
    )
    tmdb_parser_module._tmdb_cache.clear()

    tmdb_info = await tmdb_parser("你的名字", "zh", test=True)

    assert tmdb_info is not None
    assert tmdb_info.title == "你的名字。"
    assert tmdb_info.original_title == "君の名は。"
    assert tmdb_info.year == "2016"
    assert tmdb_info.last_season == 0
    assert [
        (kind, query["query"], query["language"]) for kind, query in search_requests
    ] == [
        ("tv", ["你的名字"], ["zh-CN"]),
        ("tv", ["你的名字"], ["zh-CN"]),
        ("movie", ["你的名字"], ["zh-CN"]),
    ]


async def test_tmdb_parser_is_movie_queries_movie_search_directly(mocker):
    """is_movie=True skips the TV search entirely and queries search/movie."""
    tv_search_called = False

    async def fake_get_json(url: str) -> dict:
        nonlocal tv_search_called
        if "/search/tv" in url:
            tv_search_called = True
            return {"results": [{"id": 1}]}
        if "/search/movie" in url:
            return _MOVIE_SEARCH_RESULT
        return {}

    mocker.patch.object(
        tmdb_parser_module.RequestContent, "get_json", side_effect=fake_get_json
    )
    tmdb_parser_module._tmdb_cache.clear()

    tmdb_info = await tmdb_parser("你的名字", "zh", test=True, is_movie=True)

    assert tv_search_called is False
    assert tmdb_info is not None
    assert tmdb_info.title == "你的名字。"


async def test_tv_whitespace_retry_preserves_language(mocker):
    search_requests: list[dict[str, list[str]]] = []

    async def fake_get_json(url: str) -> dict:
        if "/search/tv" in url:
            query = _query_params(url)
            search_requests.append(query)
            if query["query"] == ["海 盗 战 记"]:
                return {"results": []}
            return {"results": [{"id": 82684}]}
        if "/season/" in url:
            return {"episodes": []}
        return _SHOW_INFO

    mocker.patch.object(
        tmdb_parser_module.RequestContent, "get_json", side_effect=fake_get_json
    )
    tmdb_parser_module._tmdb_cache.clear()

    tmdb_info = await tmdb_parser("海 盗 战 记", "jp", test=True)

    assert tmdb_info is not None
    assert [query["query"] for query in search_requests] == [
        ["海 盗 战 记"],
        ["海盗战记"],
    ]
    assert [query["language"] for query in search_requests] == [
        ["ja-JP"],
        ["ja-JP"],
    ]


async def test_movie_whitespace_retry_preserves_language(mocker):
    search_requests: list[dict[str, list[str]]] = []

    async def fake_get_json(url: str) -> dict:
        if "/search/movie" not in url:
            return {}
        query = _query_params(url)
        search_requests.append(query)
        if query["query"] == ["Your Name"]:
            return {"results": []}
        return _MOVIE_SEARCH_RESULT

    mocker.patch.object(
        tmdb_parser_module.RequestContent, "get_json", side_effect=fake_get_json
    )
    tmdb_parser_module._tmdb_cache.clear()

    tmdb_info = await tmdb_parser("Your Name", "en", test=True, is_movie=True)

    assert tmdb_info is not None
    assert [query["query"] for query in search_requests] == [
        ["Your Name"],
        ["YourName"],
    ]
    assert [query["language"] for query in search_requests] == [
        ["en-US"],
        ["en-US"],
    ]


async def test_tmdb_parser_movie_search_no_results_returns_none(mocker):
    async def fake_get_json(url: str) -> dict:
        return {"results": []}

    mocker.patch.object(
        tmdb_parser_module.RequestContent, "get_json", side_effect=fake_get_json
    )
    tmdb_parser_module._tmdb_cache.clear()

    tmdb_info = await tmdb_parser("不存在的电影", "zh", test=True, is_movie=True)

    assert tmdb_info is None


@pytest.mark.parametrize(
    ("builder_name", "path"),
    (("search_url", "/3/search/tv"), ("search_movie_url", "/3/search/movie")),
)
@pytest.mark.parametrize(
    ("language", "expected_locale"),
    (("zh", "zh-CN"), ("jp", "ja-JP"), ("en", "en-US")),
)
def test_search_urls_encode_query_and_language(
    builder_name: str,
    path: str,
    language: str,
    expected_locale: str,
):
    title = "关于我转生变成史莱姆 & Friends"
    with patch.object(tmdb_parser_module, "settings") as mock_settings:
        mock_settings.network.tmdb_base_url = "https://tmdb.example/base/"
        mock_settings.network.tmdb_api_key = "custom key"
        builder = getattr(tmdb_parser_module, builder_name)
        url = builder(title, language)

    parsed = urlsplit(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "tmdb.example"
    assert parsed.path == f"/base{path}"
    assert parse_qs(parsed.query) == {
        "api_key": ["custom key"],
        "page": ["1"],
        "query": [title],
        "include_adult": ["false"],
        "language": [expected_locale],
    }


def test_search_url_uses_custom_api_key():
    """用户自配的 TMDB API key 优先于内置共享 key (#975)。"""
    with patch.object(tmdb_parser_module, "settings") as mock_settings:
        mock_settings.network.tmdb_base_url = "https://api.themoviedb.org"
        mock_settings.network.tmdb_api_key = "customkey123"
        url = tmdb_parser_module.search_url("test", "zh")

    assert _query_params(url)["api_key"] == ["customkey123"]


def test_search_url_falls_back_to_builtin_key():
    from module.conf import TMDB_API

    with patch.object(tmdb_parser_module, "settings") as mock_settings:
        mock_settings.network.tmdb_base_url = "https://api.themoviedb.org"
        mock_settings.network.tmdb_api_key = ""
        url = tmdb_parser_module.search_url("test", "zh")

    assert _query_params(url)["api_key"] == [TMDB_API]


def test_network_config_tmdb_api_key_defaults_empty():
    from module.models.config import Network

    assert Network().tmdb_api_key == ""


def test_reset_cache_clears_tmdb_cache():
    """reset_cache() must drop all cached lookups (called on config reload so
    a changed tmdb_base_url stops serving results from the old endpoint)."""
    tmdb_parser_module._tmdb_cache["stale-key"] = None
    assert len(tmdb_parser_module._tmdb_cache) > 0

    tmdb_parser_module.reset_cache()

    assert len(tmdb_parser_module._tmdb_cache) == 0


# ---------------------------------------------------------------------------
# air_date matching (used by the `mix` source parser)
# ---------------------------------------------------------------------------


def _season(number: int, air_date: str | None, poster: str) -> dict:
    return {
        "name": f"第 {number} 季",
        "air_date": air_date,
        "poster_path": poster,
        "season_number": number,
        "episode_count": 12,
    }


def _show(name: str, seasons: list[dict], first_air_date: str) -> dict:
    return {
        "genres": [{"id": 16, "name": "Animation"}],
        "name": name,
        "original_name": name,
        "first_air_date": first_air_date,
        "status": "Returning Series",
        "poster_path": "/show.jpg",
        "seasons": seasons,
    }


def _make_get_json(details: dict[int, dict], episodes: dict[int, list] | None = None):
    """Build a URL-dispatching ``get_json`` fake plus its request journal.

    Deliberately mirrors the module-level ``_fake_get_json`` style: the patch is
    non-autospec, so ``req.get_json(url)`` calls the mock with the URL only.
    """
    detail_requests: list[int] = []

    async def fake_get_json(url: str) -> dict | None:
        if "/search/tv" in url:
            return {"results": [{"id": tv_id} for tv_id in details]}
        season_match = re.search(r"/3/tv/\d+/season/(\d+)", url)
        if season_match:
            season_number = int(season_match.group(1))
            return {"episodes": (episodes or {}).get(season_number, [])}
        detail_match = re.search(r"/3/tv/(\d+)\?", url)
        if detail_match:
            tv_id = int(detail_match.group(1))
            detail_requests.append(tv_id)
            return details.get(tv_id)
        return {}

    return fake_get_json, detail_requests


_SHOW_A = _show("同名动画 A", [_season(1, "2019-04-05", "/a1.jpg")], "2019-04-05")
_SHOW_B = _show(
    "同名动画 B",
    [
        _season(1, "2023-01-06", "/b1.jpg"),
        _season(2, "2026-04-03", "/b2.jpg"),
        _season(3, "2026-07-05", "/b3.jpg"),
    ],
    "2023-01-06",
)


async def test_air_date_picks_the_candidate_whose_season_matches(mocker):
    """同名动画有多个时，放送开始日期决定选哪一部、哪一季。"""
    fake, _ = _make_get_json({1001: _SHOW_A, 1002: _SHOW_B})
    mocker.patch.object(tmdb_parser_module.RequestContent, "get_json", side_effect=fake)
    tmdb_parser_module._tmdb_cache.clear()

    info = await tmdb_parser(
        "同名动画", "zh", test=True, air_date=datetime.date(2026, 4, 3)
    )

    assert info is not None
    assert info.id == 1002  # 不是搜索结果里的第一个
    assert info.matched_season == 2
    # last_season 必须保持「TMDB 一共有几季」的语义：offset_detector 拿它做
    # 越界判断，覆盖成匹配季号会让合法的 S3 订阅误报 offset 建议。
    assert info.last_season == 3
    assert (info.poster_link or "").endswith("/b2.jpg")


async def test_air_date_beyond_window_returns_none(mocker):
    """所有候选都离目标日期太远：视为 TMDB 上没有这部番，交给调用方降级。"""
    fake, _ = _make_get_json({1001: _SHOW_A, 1002: _SHOW_B})
    mocker.patch.object(tmdb_parser_module.RequestContent, "get_json", side_effect=fake)
    tmdb_parser_module._tmdb_cache.clear()

    info = await tmdb_parser(
        "同名动画", "zh", test=True, air_date=datetime.date(2010, 1, 1)
    )

    assert info is None
    # 否定结果不缓存，避免一次瞬时故障毒化整个进程生命周期
    assert tmdb_parser_module._tmdb_cache == {}


async def test_air_date_without_comparable_dates_falls_back_to_first_animation(mocker):
    """TMDB 尚未录入放送日期（新番常见）既不是超窗口也不是没候选，
    必须退回旧行为，否则最需要日期匹配的场景反而最容易失败。"""
    undated = _show("未定档 A", [_season(1, None, "/u1.jpg")], "")
    undated_other = _show("未定档 B", [_season(1, None, "/u2.jpg")], "")
    fake, _ = _make_get_json({2001: undated, 2002: undated_other})
    mocker.patch.object(tmdb_parser_module.RequestContent, "get_json", side_effect=fake)
    tmdb_parser_module._tmdb_cache.clear()

    info = await tmdb_parser(
        "未定档", "zh", test=True, air_date=datetime.date(2026, 4, 3)
    )

    assert info is not None
    assert info.id == 2001  # 第一个动画候选
    assert info.matched_season is None


async def test_episode_dates_override_a_misleading_season_date(mocker):
    """分割放送：TMDB 把两个 cour 合成一季，季级 air_date 只记第一 cour，
    所以季级比较会选错季；集级 air_date 才落在正确的 cour 上。"""
    split_cour = _show(
        "分割放送",
        [
            _season(1, "2025-10-01", "/c1.jpg"),
            _season(2, "2026-04-05", "/c2.jpg"),
        ],
        "2025-10-01",
    )
    # S1 的第二个 cour 在 2026-01 播出，正是观测到的日期
    # get_season_episode_air_dates 自己把字符串解析成 date，这里给原始形状
    episodes = {
        1: [
            {"episode_number": 1, "air_date": "2025-10-01"},
            {"episode_number": 13, "air_date": "2026-01-09"},
        ],
        2: [{"episode_number": 1, "air_date": "2026-04-05"}],
    }
    fake, _ = _make_get_json({3001: split_cour}, episodes)
    mocker.patch.object(tmdb_parser_module.RequestContent, "get_json", side_effect=fake)
    tmdb_parser_module._tmdb_cache.clear()

    observed = datetime.date(2026, 1, 9)
    # 季级：|2025-10-01 - observed| = 100 天，|2026-04-05 - observed| = 86 天
    # → 单看季级会选错成 S2
    info = await tmdb_parser("分割放送", "zh", test=True, air_date=observed)

    assert info is not None
    assert info.matched_season == 1
    assert (info.poster_link or "").endswith("/c1.jpg")


async def test_air_date_is_part_of_the_cache_key(mocker):
    """带日期与不带日期的查询结果不同，共用缓存键会互相污染。"""
    fake, detail_requests = _make_get_json({1002: _SHOW_B})
    mocker.patch.object(tmdb_parser_module.RequestContent, "get_json", side_effect=fake)
    tmdb_parser_module._tmdb_cache.clear()

    without = await tmdb_parser("同名动画 B", "zh", test=True)
    with_date = await tmdb_parser(
        "同名动画 B", "zh", test=True, air_date=datetime.date(2026, 4, 3)
    )

    assert without is not None and without.matched_season is None
    assert with_date is not None and with_date.matched_season == 2
    assert len(tmdb_parser_module._tmdb_cache) == 2
    # 第二次没有命中缓存，确实重新查询了
    assert len(detail_requests) == 2


async def test_without_air_date_behaviour_is_unchanged(mocker):
    """不传 air_date 时仍是「取第一个动画」，且详情只拉一次。"""
    fake, detail_requests = _make_get_json({1001: _SHOW_A, 1002: _SHOW_B})
    mocker.patch.object(tmdb_parser_module.RequestContent, "get_json", side_effect=fake)
    tmdb_parser_module._tmdb_cache.clear()

    info = await tmdb_parser("同名动画", "zh", test=True)

    assert info is not None
    assert info.id == 1001
    assert info.matched_season is None
    # 命中即停，且不再像改动前那样对同一个 URL 重复请求两次
    assert detail_requests == [1001]


async def test_movie_air_date_sorts_but_never_filters(mocker):
    """剧场版的日期只用来在候选间排序：Mikan 的「放送开始」多是资源发布日，
    与 TMDB 的院线上映日常差数月到数年，硬过滤会把正确的电影滤掉。"""
    results = {
        "results": [
            {
                "id": 1,
                "title": "错误的同名电影",
                "original_title": "wrong",
                "release_date": "2001-01-01",
                "poster_path": "/wrong.jpg",
            },
            {
                "id": 2,
                "title": "正确的电影",
                "original_title": "right",
                "release_date": "2024-08-16",
                "poster_path": "/right.jpg",
            },
        ]
    }

    async def fake_get_json(url: str) -> dict:
        return results if "/search/movie" in url else {"results": []}

    mocker.patch.object(
        tmdb_parser_module.RequestContent, "get_json", side_effect=fake_get_json
    )
    tmdb_parser_module._tmdb_cache.clear()

    # 距离 2024-08-16 超过一年，远超剧集侧的选番窗口，但仍必须返回它
    info = await tmdb_parser(
        "电影", "zh", test=True, is_movie=True, air_date=datetime.date(2026, 3, 1)
    )

    assert info is not None
    assert info.title == "正确的电影"


@pytest.mark.live
@pytest.mark.skipif(
    not os.environ.get("RUN_LIVE_TMDB_TESTS"),
    reason="hits the real TMDB API; set RUN_LIVE_TMDB_TESTS=1 to run",
)
async def test_tmdb_parser_live():
    tmdb_parser_module._tmdb_cache.clear()

    bangumi_title = "海盗战记"
    bangumi_year = "2019"
    bangumi_season = 2

    tmdb_info = await tmdb_parser(bangumi_title, "zh", test=True)

    assert tmdb_info is not None
    assert tmdb_info.title == "冰海战记"
    assert tmdb_info.year == bangumi_year
    assert tmdb_info.last_season == bangumi_season


# --- 放送星期推导 -------------------------------------------------------


def _episodes(*dates: str) -> list[dict]:
    return [
        {"episode_number": i, "air_date": datetime.date.fromisoformat(d)}
        for i, d in enumerate(dates, start=1)
    ]


_DERIVE = tmdb_parser_module._derive_air_weekday


def test_derive_air_weekday_prefers_next_episode():
    """下一集的播出日就是日历要展示的当周档期，优先级最高。"""
    info = {
        "next_episode_to_air": {"air_date": "2026-04-03"},  # 周五
        "last_episode_to_air": {"air_date": "2026-03-25"},  # 周三
        "first_air_date": "2019-07-08",  # 周一
    }

    assert _DERIVE(info, [], []) == 4


def test_derive_air_weekday_falls_back_to_last_episode():
    """完结番没有 next_episode_to_air，用最后一集。"""
    info = {
        "next_episode_to_air": None,
        "last_episode_to_air": {"air_date": "2026-03-25"},  # 周三
        "first_air_date": "2019-07-08",
    }

    assert _DERIVE(info, [], []) == 2


def test_derive_air_weekday_uses_episode_mode():
    """两个 episode_to_air 都没有时，用最高季最近几集的众数。"""
    info = {"first_air_date": "2019-07-08"}  # 周一，不该被用到
    season_nums = [(1, 12), (2, 6)]
    episode_results = [
        _episodes("2019-07-08"),  # 低季号，不参与
        _episodes(
            "2026-01-07",  # 周三——挪档的特别篇
            "2026-01-09",  # 以下均为周五
            "2026-01-16",
            "2026-01-23",
            "2026-01-30",
            "2026-02-06",
        ),
    ]

    assert _DERIVE(info, season_nums, episode_results) == 4


def test_derive_air_weekday_rejects_inconclusive_mode():
    """档期本身不规律时返回 None，交给下一级来源，不硬猜。"""
    info: dict = {}
    season_nums = [(1, 4)]
    episode_results = [
        _episodes("2026-01-05", "2026-01-07", "2026-01-09", "2026-01-11")
    ]

    assert _DERIVE(info, season_nums, episode_results) is None


def test_derive_air_weekday_falls_back_to_latest_season_air_date():
    info = {
        "seasons": [
            {"name": "第 1 季", "air_date": "2019-07-08", "season_number": 1},
            {"name": "第 2 季", "air_date": "2023-01-09", "season_number": 2},
        ],
        "first_air_date": "2019-07-10",
    }

    assert _DERIVE(info, [], []) == 0  # 2023-01-09 是周一


def test_derive_air_weekday_ignores_special_seasons():
    """特别篇季不参与季级兜底（与 get_season 的口径一致）。"""
    info = {
        "seasons": [
            {"name": "第 1 季", "air_date": "2026-01-09", "season_number": 1},
            {"name": "特别篇", "air_date": "2026-01-11", "season_number": 2},
        ],
    }

    assert _DERIVE(info, [], []) == 4  # 周五，不是特别篇的周日


def test_derive_air_weekday_last_resort_is_first_air_date():
    assert _DERIVE({"first_air_date": "2019-07-08"}, [], []) == 0


def test_derive_air_weekday_returns_none_without_any_date():
    assert _DERIVE({}, [], []) is None


def test_derive_air_weekday_ignores_malformed_dates():
    """空串/非法日期不能抛异常，逐级降级即可。"""
    info = {
        "next_episode_to_air": {"air_date": ""},
        "last_episode_to_air": {"air_date": "not-a-date"},
        "first_air_date": "2019-07-08",
    }

    assert _DERIVE(info, [], []) == 0


def test_derive_air_weekday_skips_failed_season_requests():
    """gather 的异常项必须被跳过，不能参与推导。"""
    info = {"first_air_date": "2019-07-08"}
    season_nums = [(1, 12)]

    assert _DERIVE(info, season_nums, [RuntimeError("boom")]) == 0


async def test_tmdb_parser_exposes_air_weekday(mocker):
    """整条链路把星期挂到 TMDBInfo 上，供日历使用。"""

    async def fake_get_json(url: str) -> dict:
        if "/search/tv" in url:
            return {"results": [{"id": 82684}]}
        if "/season/" in url:
            return {"episodes": []}
        return {**_SHOW_INFO, "next_episode_to_air": {"air_date": "2026-04-03"}}

    mocker.patch.object(
        tmdb_parser_module.RequestContent, "get_json", side_effect=fake_get_json
    )
    tmdb_parser_module._tmdb_cache.clear()

    info = await tmdb_parser("海盗战记", "zh", test=True)

    assert info is not None
    assert info.air_weekday == 4  # 2026-04-03 是周五


async def test_movie_has_no_air_weekday(mocker):
    """剧场版没有周档期。"""

    async def fake_get_json(url: str) -> dict:
        if "/search/movie" in url:
            return {
                "results": [{"id": 1, "title": "电影", "release_date": "2024-08-16"}]
            }
        return {}

    mocker.patch.object(
        tmdb_parser_module.RequestContent, "get_json", side_effect=fake_get_json
    )
    tmdb_parser_module._tmdb_cache.clear()

    info = await tmdb_parser("电影", "zh", test=True, is_movie=True)

    assert info is not None
    assert info.air_weekday is None
