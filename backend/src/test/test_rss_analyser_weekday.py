"""入库时顺手写放送星期（仅「优先解析器」模式）。

新番首次入库时解析器本来就要抓一次 Mikan 主页 / 查一次 TMDB，星期是顺带的
产物，所以在这里写掉，新订阅立刻就有星期，不用等日历的 24h 定时任务。

两条纪律：``weekday_source`` 为 bgm 时一行网络代码都不多跑；这条路径**不回退
bgm.tv**——拿不到就留空，交给日历刷新去补。
"""

import datetime
from unittest.mock import AsyncMock, patch

import pytest

from module.conf import settings
from module.parser.analyser.mikan_parser import MikanInfo
from module.parser.analyser.mix_parser import MixResult
from module.rss.analyser import RSSAnalyser
from test.factories import make_bangumi, make_rss_item, make_torrent

MIKAN_INFO = MikanInfo(
    poster_link="posters/a.jpg",
    official_title="Test Anime",
    air_date=datetime.date(2026, 4, 3),
    air_weekday=4,
)


@pytest.fixture
def parser_mode(monkeypatch):
    def _set(value: str):
        monkeypatch.setattr(settings.rss_parser, "weekday_source", value)

    return _set


@pytest.fixture
def seams():
    """Patch every parser seam the ingest path can reach."""
    with (
        patch(
            "module.rss.analyser.TitleParser.mikan_parser",
            new_callable=AsyncMock,
            return_value=MIKAN_INFO,
        ) as mikan,
        patch(
            "module.rss.analyser.TitleParser.tmdb_parser",
            new_callable=AsyncMock,
            return_value=("TMDB Title", 2, "2026", "posters/t.jpg"),
        ) as tmdb,
        patch(
            "module.rss.analyser.mix_parser",
            new_callable=AsyncMock,
            return_value=MixResult(official_title="Mix Title", air_weekday=6),
        ) as mix,
        patch(
            "module.rss.analyser.parser_weekday",
            new_callable=AsyncMock,
            return_value=2,
        ) as weekday,
    ):
        yield {"mikan": mikan, "tmdb": tmdb, "mix": mix, "weekday": weekday}


async def _parse(parser: str, **bangumi_overrides):
    bangumi = make_bangumi(air_weekday=None, **bangumi_overrides)
    await RSSAnalyser().official_title_parser(
        bangumi=bangumi,
        rss=make_rss_item(parser=parser),
        torrent=make_torrent(),
    )
    return bangumi


class TestParserMode:
    async def test_mikan_reuses_the_weekday_it_already_fetched(
        self, parser_mode, seams
    ):
        """Mikan 主页那一次抓取同时给出海报、标题和星期，零额外请求。"""
        parser_mode("parser")

        bangumi = await _parse("mikan")

        assert bangumi.air_weekday == 4
        seams["weekday"].assert_not_awaited()

    async def test_mix_passes_the_weekday_through(self, parser_mode, seams):
        parser_mode("parser")

        bangumi = await _parse("mix")

        assert bangumi.air_weekday == 6
        seams["weekday"].assert_not_awaited()

    async def test_tmdb_queries_with_the_original_title(self, parser_mode, seams):
        """TMDB 缓存以查询词为键，而 official_title 会被覆盖成 TMDB 的标准名，
        必须用覆盖前的原查询词去查，否则每部番都多打一次 TMDB。"""
        parser_mode("parser")

        bangumi = await _parse("tmdb", official_title="Original Query")

        assert bangumi.air_weekday == 2
        assert seams["weekday"].await_args.kwargs["official_title"] == "Original Query"

    async def test_movies_have_no_weekday(self, parser_mode, seams):
        parser_mode("parser")
        seams["mix"].return_value = MixResult(official_title="A Movie", air_weekday=3)

        bangumi = await _parse("mix", episode_type="movie")

        assert bangumi.air_weekday is None

    async def test_never_falls_back_to_bgm(self, parser_mode, seams):
        """入库路径不碰 bgm.tv：拿不到就留空，交给日历刷新。"""
        parser_mode("parser")
        seams["mikan"].return_value = MikanInfo("posters/a.jpg", "Test Anime")

        with patch(
            "module.parser.analyser.bgm_calendar.fetch_bgm_calendar",
            new_callable=AsyncMock,
        ) as bgm:
            bangumi = await _parse("mikan")

        assert bangumi.air_weekday is None
        bgm.assert_not_awaited()


class TestBgmMode:
    async def test_no_weekday_work_when_bgm_is_preferred(self, parser_mode, seams):
        """默认配置下入库路径与改动前完全一致。"""
        parser_mode("bgm")

        bangumi = await _parse("mikan")

        assert bangumi.air_weekday is None
        assert bangumi.poster_link == "posters/a.jpg"  # 海报/标题照常
        seams["weekday"].assert_not_awaited()

    async def test_tmdb_path_skips_the_weekday_lookup(self, parser_mode, seams):
        parser_mode("bgm")

        bangumi = await _parse("tmdb")

        assert bangumi.air_weekday is None
        seams["weekday"].assert_not_awaited()


class TestIsolation:
    async def test_search_preview_does_no_weekday_work(self, parser_mode, seams):
        """fetch_poster=False 是交互式搜索的快速路径，不该做任何网络抓取。"""
        parser_mode("parser")
        bangumi = make_bangumi(air_weekday=None)

        await RSSAnalyser().official_title_parser(
            bangumi=bangumi,
            rss=make_rss_item(parser="mikan"),
            torrent=make_torrent(),
            fetch_poster=False,
        )

        assert bangumi.air_weekday is None
        seams["mikan"].assert_not_awaited()
        seams["weekday"].assert_not_awaited()
