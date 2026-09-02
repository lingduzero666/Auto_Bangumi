"""Tests for TorrentManager rule management.

删除番剧时应停用其独立订阅（aggregate=False）的孤儿 RSS 条目（停用而非删除：
无法区分搜索订阅与用户手动添加的独立订阅，删除会连带清掉其他番剧的种子
去重记录）；聚合订阅与仍被其他番剧引用的订阅不受影响。
"""

from unittest.mock import AsyncMock, patch

from sqlmodel import select

from module.database import Database
from module.manager import TorrentManager
from module.models import RenameOperation
from test.factories import make_bangumi, make_rss_item

SUB_URL = "https://mikanani.me/RSS/Bangumi?bangumiId=9&subgroupid=1"


async def _seed(db: Database, *, bangumi=(), rss=()):
    for b in bangumi:
        await db.bangumi.add(b)
    for r in rss:
        await db.rss.add(r)


class TestDeleteRuleRenameOperationCleanup:
    """删除番剧时一并回收它的重命名记录。

    这些行在番剧和种子都删掉后只剩垃圾价值：占着身份索引，还会让冲突列表
    指向一个已经不存在的番剧。重新添加同一种子后是否重跑改名由
    ``get_or_create`` 的陈旧判定负责，与这里的回收互不依赖。
    """

    @staticmethod
    def _operation(bangumi_id: int, episode: int) -> RenameOperation:
        return RenameOperation(
            downloader_type="qbittorrent",
            kind="conflict",
            state="done",
            new_task_id=f"hash-{bangumi_id}-{episode}",
            save_path="/downloads/Anime/Season 1",
            source_path=f"raw-{episode}.mkv",
            target_path=f"Anime S01E0{episode}.mkv",
            bangumi_id=bangumi_id,
        )

    async def test_delete_rule_removes_only_that_bangumi_rows(self, db_engine):
        async with Database(engine=db_engine) as db:
            await _seed(
                db,
                bangumi=[
                    make_bangumi(id=1, rss_link=SUB_URL),
                    make_bangumi(
                        id=2,
                        official_title="Other Anime",
                        title_raw="Other Anime Raw",
                        rss_link=SUB_URL,
                    ),
                ],
            )
            db.session.add(self._operation(1, 1))
            db.session.add(self._operation(2, 1))
            await db.commit()

            await manager_delete(db, 1)

            rows = (await db.session.execute(select(RenameOperation))).scalars().all()
            assert [row.bangumi_id for row in rows] == [2], "只该删掉被删番剧的记录"


async def manager_delete(db: Database, bangumi_id: int):
    return await TorrentManager(db).delete_rule(bangumi_id, file=False)


class TestDeleteRuleRSSCleanup:
    async def test_delete_rule_disables_orphan_sub_rss(self, db_engine):
        async with Database(engine=db_engine) as db:
            await _seed(
                db,
                bangumi=[make_bangumi(id=1, rss_link=SUB_URL)],
                rss=[make_rss_item(url=SUB_URL, aggregate=False)],
            )
            manager = TorrentManager(db)

            resp = await manager.delete_rule(1, file=False)

            assert resp.status is True
            rss_item = await db.rss.search_url(SUB_URL)
            assert rss_item is not None
            assert rss_item.enabled is False

    async def test_delete_rule_keeps_aggregate_rss_enabled(self, db_engine):
        async with Database(engine=db_engine) as db:
            await _seed(
                db,
                bangumi=[make_bangumi(id=1, rss_link=SUB_URL)],
                rss=[make_rss_item(url=SUB_URL, aggregate=True)],
            )
            manager = TorrentManager(db)

            await manager.delete_rule(1, file=False)

            rss_item = await db.rss.search_url(SUB_URL)
            assert rss_item is not None
            assert rss_item.enabled is True

    async def test_delete_rule_keeps_rss_referenced_by_other_bangumi(self, db_engine):
        async with Database(engine=db_engine) as db:
            await _seed(
                db,
                bangumi=[
                    make_bangumi(id=1, rss_link=SUB_URL),
                    make_bangumi(
                        id=2,
                        official_title="Other Anime",
                        title_raw="Other Anime Raw",
                        rss_link=f"https://example.com/other,{SUB_URL}",
                    ),
                ],
                rss=[make_rss_item(url=SUB_URL, aggregate=False)],
            )
            manager = TorrentManager(db)

            await manager.delete_rule(1, file=False)

            rss_item = await db.rss.search_url(SUB_URL)
            assert rss_item is not None
            assert rss_item.enabled is True

    async def test_delete_rule_comma_joined_link_disables_each_orphan_feed(
        self, db_engine
    ):
        url_a = "https://mikanani.me/RSS/Bangumi?bangumiId=1"
        url_b = "https://mikanani.me/RSS/Bangumi?bangumiId=2"
        async with Database(engine=db_engine) as db:
            await _seed(
                db,
                bangumi=[make_bangumi(id=1, rss_link=f"{url_a},{url_b}")],
                rss=[
                    make_rss_item(url=url_a, aggregate=False),
                    make_rss_item(url=url_b, aggregate=False),
                ],
            )
            manager = TorrentManager(db)

            await manager.delete_rule(1, file=False)

            for url in (url_a, url_b):
                rss_item = await db.rss.search_url(url)
                assert rss_item is not None
                assert rss_item.enabled is False

    async def test_delete_rule_keeps_orphan_feed_torrent_history(self, db_engine):
        """停用而非删除：不应连带清掉该 RSS 关联的种子去重记录。"""
        from module.models import Torrent

        async with Database(engine=db_engine) as db:
            await _seed(
                db,
                bangumi=[make_bangumi(id=1, rss_link=SUB_URL)],
                rss=[make_rss_item(url=SUB_URL, aggregate=False)],
            )
            rss_item = await db.rss.search_url(SUB_URL)
            assert rss_item is not None
            await db.torrent.add(
                Torrent(name="ep01", url="https://example.com/1", rss_id=rss_item.id)
            )
            manager = TorrentManager(db)

            await manager.delete_rule(1, file=False)

            assert len(await db.torrent.search_rss(rss_item.id)) == 1


class TestTorrentMatching:
    async def test_match_torrents_list_normalizes_save_path_separators(self):
        """qBittorrent-on-Windows returns backslashes; DB paths may use slashes."""
        bangumi = make_bangumi(
            save_path="D:/Downloads/Bangumi/Test Anime (2024)/Season 1"
        )
        mock_client = AsyncMock()
        mock_client.get_torrent_info = AsyncMock(
            return_value=[
                {
                    "hash": "matched",
                    "save_path": "D:\\Downloads\\Bangumi\\Test Anime (2024)\\Season 1\\",
                },
                {
                    "hash": "other",
                    "save_path": "D:/Downloads/Bangumi/Other/Season 1",
                },
            ]
        )

        with patch("module.manager.torrent.DownloadClient") as mock_download_client:
            mock_download_client.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_download_client.return_value.__aexit__ = AsyncMock(return_value=False)

            match_torrents_list = getattr(
                TorrentManager, "_TorrentManager__match_torrents_list"
            )
            hashes = await match_torrents_list(bangumi)

        assert hashes == ["matched"]
