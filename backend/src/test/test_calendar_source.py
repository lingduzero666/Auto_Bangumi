"""Tests for TorrentManager.refresh_calendar's two air-weekday sources.

放送星期有两个来源：bgm.tv 的 /calendar（批量，标题模糊匹配）与解析器（逐条，
按订阅自己的 parser 取数）。``rss_parser.weekday_source`` 决定先问谁，另一个
作兜底。最重要的保证是「懒回退」：默认的 bgm 优先且全部命中时，解析器路径一个
请求都不发，老用户升级后开销与行为都不变。
"""

from unittest.mock import AsyncMock, patch

import pytest

from module.conf import settings
from module.database import Database
from module.manager import TorrentManager
from module.models import Bangumi
from test.factories import make_bangumi, make_rss_item, make_torrent

SUB_URL = "https://mikanani.me/RSS/Bangumi?bangumiId=9&subgroupid=1"
EPISODE_URL = "https://mikanani.me/Home/Episode/abc"


def _calendar(*titles: str, weekday: int = 3) -> list[dict]:
    return [{"name": t, "name_cn": t, "air_weekday": weekday} for t in titles]


@pytest.fixture
def weekday_source(monkeypatch):
    """Set rss_parser.weekday_source for the duration of a test."""

    def _set(value: str):
        monkeypatch.setattr(settings.rss_parser, "weekday_source", value)

    return _set


@pytest.fixture
def sources():
    """Patch both sources at their use site in the manager module."""
    with (
        patch(
            "module.manager.torrent.fetch_bgm_calendar", new_callable=AsyncMock
        ) as bgm,
        patch(
            "module.manager.torrent.parser_weekday", new_callable=AsyncMock
        ) as parser,
    ):
        bgm.return_value = []
        parser.return_value = None
        yield bgm, parser


async def _seed(db: Database, *, bangumi=(), rss=(), torrents=()):
    for b in bangumi:
        await db.bangumi.add(b)
    for r in rss:
        await db.rss.add(r)
    for t in torrents:
        await db.torrent.add(t)


async def _mikan_setup(db: Database, **bangumi_overrides):
    """One anime subscribed through a Mikan feed, with a torrent carrying a
    homepage — the shape the parser path needs end to end."""
    await _seed(
        db,
        bangumi=[make_bangumi(id=1, rss_link=SUB_URL, **bangumi_overrides)],
        rss=[make_rss_item(id=1, url=SUB_URL, parser="mikan")],
        torrents=[make_torrent(bangumi_id=1, rss_id=1, homepage=EPISODE_URL)],
    )


class TestSourcePriority:
    async def test_bgm_first_never_touches_parsers_when_fully_matched(
        self, db_engine, weekday_source, sources
    ):
        """默认配置下的核心保证：bgm 全命中时解析器零调用。"""
        bgm, parser = sources
        weekday_source("bgm")
        bgm.return_value = _calendar("Test Anime", weekday=3)
        async with Database(engine=db_engine) as db:
            await _mikan_setup(db)

            resp = await TorrentManager(db).refresh_calendar()

            assert resp.status_code == 200
            parser.assert_not_awaited()
            assert (await db.bangumi.search_id(1)).air_weekday == 3

    async def test_bgm_first_falls_back_to_parsers_for_unmatched(
        self, db_engine, weekday_source, sources
    ):
        """bgm 匹配不到的番剧才走解析器——这正好修掉日历的「未知」列。"""
        bgm, parser = sources
        weekday_source("bgm")
        bgm.return_value = _calendar("Some Other Anime", weekday=3)
        parser.return_value = 5
        async with Database(engine=db_engine) as db:
            await _mikan_setup(db)

            resp = await TorrentManager(db).refresh_calendar()

            assert resp.status_code == 200
            parser.assert_awaited_once()
            assert (await db.bangumi.search_id(1)).air_weekday == 5

    async def test_parser_first_never_touches_bgm_when_fully_resolved(
        self, db_engine, weekday_source, sources
    ):
        bgm, parser = sources
        weekday_source("parser")
        parser.return_value = 2
        async with Database(engine=db_engine) as db:
            await _mikan_setup(db)

            resp = await TorrentManager(db).refresh_calendar()

            assert resp.status_code == 200
            bgm.assert_not_awaited()
            assert (await db.bangumi.search_id(1)).air_weekday == 2

    async def test_parser_first_falls_back_to_bgm(
        self, db_engine, weekday_source, sources
    ):
        bgm, parser = sources
        weekday_source("parser")
        parser.return_value = None
        bgm.return_value = _calendar("Test Anime", weekday=6)
        async with Database(engine=db_engine) as db:
            await _mikan_setup(db)

            resp = await TorrentManager(db).refresh_calendar()

            assert resp.status_code == 200
            bgm.assert_awaited_once()
            assert (await db.bangumi.search_id(1)).air_weekday == 6


class TestParserDispatch:
    @pytest.mark.parametrize("parser_type", ["mikan", "mix", "tmdb", "parser"])
    async def test_dispatches_the_subscription_parser(
        self, db_engine, weekday_source, sources, parser_type
    ):
        """解析器来源按订阅自己的 parser 取数，原样传给 parser_weekday。"""
        _, parser = sources
        weekday_source("parser")
        parser.return_value = 1
        async with Database(engine=db_engine) as db:
            await _seed(
                db,
                bangumi=[make_bangumi(id=1, rss_link=SUB_URL)],
                rss=[make_rss_item(id=1, url=SUB_URL, parser=parser_type)],
                torrents=[make_torrent(bangumi_id=1, rss_id=1, homepage=EPISODE_URL)],
            )

            await TorrentManager(db).refresh_calendar()

            assert parser.await_args.kwargs["parser"] == parser_type

    async def test_passes_the_mikan_homepage(self, db_engine, weekday_source, sources):
        _, parser = sources
        weekday_source("parser")
        async with Database(engine=db_engine) as db:
            await _mikan_setup(db)

            await TorrentManager(db).refresh_calendar()

            assert parser.await_args.kwargs["episode_homepage"] == EPISODE_URL

    async def test_bangumi_without_torrent_has_no_homepage(
        self, db_engine, weekday_source, sources
    ):
        """老数据/从未下载过的番剧没有种子记录，homepage 为空但仍要尝试。"""
        _, parser = sources
        weekday_source("parser")
        async with Database(engine=db_engine) as db:
            await _seed(
                db,
                bangumi=[make_bangumi(id=1, rss_link=SUB_URL)],
                rss=[make_rss_item(id=1, url=SUB_URL, parser="mikan")],
            )

            await TorrentManager(db).refresh_calendar()

            assert parser.await_args.kwargs["episode_homepage"] is None

    async def test_falls_back_to_the_rss_link_when_the_feed_is_gone(
        self, db_engine, weekday_source, sources
    ):
        """订阅被删除后只剩 Bangumi.rss_link，按域名猜 mikan。"""
        _, parser = sources
        weekday_source("parser")
        async with Database(engine=db_engine) as db:
            await _seed(db, bangumi=[make_bangumi(id=1, rss_link=SUB_URL)])

            await TorrentManager(db).refresh_calendar()

            assert parser.await_args.kwargs["parser"] == "mikan"


class TestSkipRules:
    async def test_locked_and_deleted_are_never_touched(
        self, db_engine, weekday_source, sources
    ):
        """手动设置的放送日（weekday_locked）永远优先，删除的番剧不参与。"""
        bgm, parser = sources
        weekday_source("parser")
        parser.return_value = 4
        bgm.return_value = _calendar("Test Anime", weekday=4)
        async with Database(engine=db_engine) as db:
            await _seed(
                db,
                bangumi=[
                    make_bangumi(
                        id=1,
                        official_title="Locked",
                        title_raw="Locked",
                        air_weekday=0,
                        weekday_locked=True,
                    ),
                    make_bangumi(
                        id=2,
                        official_title="Deleted",
                        title_raw="Deleted",
                        air_weekday=1,
                        deleted=True,
                    ),
                ],
            )

            await TorrentManager(db).refresh_calendar()

            assert (await db.bangumi.search_id(1)).air_weekday == 0
            # 已删除的番剧被 search_id/search_all 过滤掉了，直接读行
            deleted = await db.bangumi.session.get(Bangumi, 2)
            assert deleted is not None and deleted.air_weekday == 1
            # 两个都被跳过 => 没有目标，两个来源都不该被问
            parser.assert_not_awaited()
            bgm.assert_not_awaited()

    async def test_archived_skips_parsers_but_still_matches_bgm(
        self, db_engine, weekday_source, sources
    ):
        """归档番剧不上日历，不值得为它花一次抓取；但 bgm 的内存匹配是免费的。"""
        bgm, parser = sources
        weekday_source("parser")
        bgm.return_value = _calendar("Test Anime", weekday=6)
        async with Database(engine=db_engine) as db:
            await _mikan_setup(db, archived=True)

            await TorrentManager(db).refresh_calendar()

            parser.assert_not_awaited()
            assert (await db.bangumi.search_id(1)).air_weekday == 6


class TestResilience:
    async def test_one_failing_lookup_does_not_stop_the_batch(
        self, db_engine, weekday_source, sources
    ):
        bgm, parser = sources
        weekday_source("parser")
        parser.side_effect = [RuntimeError("boom"), 5]
        async with Database(engine=db_engine) as db:
            await _seed(
                db,
                bangumi=[
                    make_bangumi(id=1, official_title="A", title_raw="A raw"),
                    make_bangumi(id=2, official_title="B", title_raw="B raw"),
                ],
                rss=[make_rss_item(id=1, url=SUB_URL, parser="mikan")],
            )

            resp = await TorrentManager(db).refresh_calendar()

            assert resp.status_code == 200
            assert (await db.bangumi.search_id(2)).air_weekday == 5

    async def test_both_sources_failing_reports_an_error(
        self, db_engine, weekday_source, sources
    ):
        weekday_source("bgm")
        async with Database(engine=db_engine) as db:
            await _mikan_setup(db)

            resp = await TorrentManager(db).refresh_calendar()

            assert resp.status_code == 500
            assert resp.status is False

    async def test_a_blocked_bgm_still_succeeds_via_parsers(
        self, db_engine, weekday_source, sources
    ):
        """bgm.tv 被墙时不能报 500，否则 calendar_tick 每天告警一次。"""
        bgm, parser = sources
        weekday_source("parser")
        parser.return_value = 3
        bgm.return_value = []
        async with Database(engine=db_engine) as db:
            await _mikan_setup(db)

            resp = await TorrentManager(db).refresh_calendar()

            assert resp.status_code == 200

    async def test_nothing_to_refresh_is_not_an_error(
        self, db_engine, weekday_source, sources
    ):
        bgm, parser = sources
        weekday_source("bgm")
        async with Database(engine=db_engine) as db:
            await _seed(db, bangumi=[make_bangumi(id=1, deleted=True)])

            resp = await TorrentManager(db).refresh_calendar()

            assert resp.status_code == 200
            bgm.assert_not_awaited()
            parser.assert_not_awaited()

    async def test_concurrency_is_capped(self, db_engine, weekday_source, sources):
        """并发上限保护 Mikan 这类社区小站与 TMDB 的共享 key。"""
        from module.manager.torrent import CALENDAR_PARSER_CONCURRENCY

        _, parser = sources
        weekday_source("parser")
        in_flight = 0
        peak = 0

        async def track(**_kwargs):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            try:
                return 1
            finally:
                in_flight -= 1

        parser.side_effect = track
        async with Database(engine=db_engine) as db:
            await _seed(
                db,
                bangumi=[
                    make_bangumi(
                        id=i, official_title=f"Anime {i}", title_raw=f"Anime {i} raw"
                    )
                    for i in range(1, 21)
                ],
            )

            await TorrentManager(db).refresh_calendar()

            assert peak <= CALENDAR_PARSER_CONCURRENCY


class TestWriteBack:
    async def test_only_changed_rows_are_written(
        self, db_engine, weekday_source, sources
    ):
        """已经是这个星期的番剧不该被重复写库。"""
        bgm, _ = sources
        weekday_source("bgm")
        bgm.return_value = _calendar("Test Anime", weekday=3)
        async with Database(engine=db_engine) as db:
            await _seed(db, bangumi=[make_bangumi(id=1, air_weekday=3)])
            with patch.object(
                db.bangumi, "update_all", new_callable=AsyncMock
            ) as update_all:
                await TorrentManager(db).refresh_calendar()

            update_all.assert_not_awaited()

    async def test_recomputes_anime_that_already_have_a_weekday(
        self, db_engine, weekday_source, sources
    ):
        """全量重算，与 bgm 模式一贯的语义一致：切换来源后旧值会被纠正。"""
        _, parser = sources
        weekday_source("parser")
        parser.return_value = 6
        async with Database(engine=db_engine) as db:
            await _mikan_setup(db, air_weekday=1)

            await TorrentManager(db).refresh_calendar()

            assert (await db.bangumi.search_id(1)).air_weekday == 6

    async def test_unresolved_anime_keep_their_current_weekday(
        self, db_engine, weekday_source, sources
    ):
        """两个来源都匹配不到时保留原值，不清空。"""
        bgm, _ = sources
        weekday_source("bgm")
        bgm.return_value = _calendar("Some Other Anime")
        async with Database(engine=db_engine) as db:
            await _seed(db, bangumi=[make_bangumi(id=1, air_weekday=2)])

            await TorrentManager(db).refresh_calendar()

            assert (await db.bangumi.search_id(1)).air_weekday == 2
