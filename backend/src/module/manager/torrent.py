import asyncio
import logging
from urllib.parse import urlparse

from module.conf import settings
from module.database import Database
from module.database.bangumi import normalize_save_path
from module.downloader import DownloadClient
from module.downloader.path import gen_save_path
from module.downloader.rules import build_rss_rule
from module.models import Bangumi, BangumiUpdate, ResponseModel
from module.parser import TitleParser
from module.parser.analyser.bgm_calendar import fetch_bgm_calendar, match_weekday
from module.parser.analyser.tmdb_parser import tmdb_parser
from module.parser.analyser.weekday_source import parser_weekday

logger = logging.getLogger(__name__)

# 日历刷新时并发抓 Mikan 主页 / TMDB 的上限。取 5 而不是更高：Mikan 是社区
# 小站，TMDB 内置共享 key 本来就有限流问题（见 models/config.py 的 #975），
# 而这是 24h 一跳的后台任务，慢一点无所谓。
CALENDAR_PARSER_CONCURRENCY = 5


class TorrentManager:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    async def __match_torrents_list(data: Bangumi | BangumiUpdate) -> list:
        async with DownloadClient() as client:
            torrents = await client.get_torrent_info(status_filter=None)
        target_save_path = normalize_save_path(data.save_path)
        return [
            torrent.get("hash", torrent.get("infohash_v1", ""))
            for torrent in torrents
            if normalize_save_path(torrent.get("save_path")) == target_save_path
        ]

    async def delete_torrents(self, data: Bangumi, client: DownloadClient):
        hash_list = await self.__match_torrents_list(data)
        if hash_list:
            if not await client.delete_torrent(hash_list):
                return ResponseModel(
                    status_code=500,
                    status=False,
                    msg_en=f"Failed to delete torrents for {data.official_title}",
                    msg_zh=f"删除 {data.official_title} 种子失败",
                )
            logger.info(f"Delete rule and torrents for {data.official_title}")
            return ResponseModel(
                status_code=200,
                status=True,
                msg_en=f"Delete rule and torrents for {data.official_title}",
                msg_zh=f"删除 {data.official_title} 规则和种子",
            )
        else:
            return ResponseModel(
                status_code=406,
                status=False,
                msg_en=f"Can't find torrents for {data.official_title}",
                msg_zh=f"无法找到 {data.official_title} 的种子",
            )

    async def _disable_orphan_sub_rss(self, data: Bangumi) -> None:
        """停用该番剧独立订阅（aggregate=False）且不再被引用的 RSS 条目（#1053）。

        停用而非删除：无法区分搜索订阅与用户手动添加的独立订阅，删除还会
        连带清掉该 rss_id 下可能属于其他番剧的种子去重记录。聚合订阅由多个
        番剧共享，永不在此处停用；rss_link 可能是逗号拼接的多个链接
        （见 BangumiDatabase.match_list），逐个拆分精确匹配。
        """
        urls = {u.strip() for u in (data.rss_link or "").split(",") if u.strip()}
        if not urls:
            return
        # 含软删除（deleted=True）的番剧：其订阅需保留以便重新启用
        still_referenced: set[str] = set()
        for bangumi in await self.db.bangumi.search_all():
            still_referenced.update(
                u.strip() for u in (bangumi.rss_link or "").split(",") if u.strip()
            )
        orphan_ids: list[int] = []
        for url in urls - still_referenced:
            rss_item = await self.db.rss.search_url(url)
            if rss_item and not rss_item.aggregate and rss_item.enabled:
                orphan_ids.append(rss_item.id)
                logger.info(f"[Manager] Disable orphan RSS feed {url}")
        if orphan_ids:
            await self.db.rss.disable_batch(orphan_ids)

    async def delete_rule(self, _id: int | str, file: bool = False):
        data = await self.db.bangumi.search_id(int(_id))
        if isinstance(data, Bangumi):
            # Clean up torrent records so re-adding the same anime can re-download
            await self.db.torrent.delete_by_bangumi_id(int(_id))
            # 同理回收重命名记录，否则它们会一直留到 prune_done 的 30 天兜底
            await self.db.rename_operation.delete_by_bangumi_id(int(_id))
            await self.db.bangumi.delete_one(int(_id))
            # 番剧删除后停用其独立订阅的孤儿 RSS；聚合订阅不受影响
            await self._disable_orphan_sub_rss(data)
            torrent_message = None
            if file:
                # Only the file-cleanup path needs the downloader, so an
                # unreachable downloader shouldn't block a DB-only delete.
                async with DownloadClient() as client:
                    torrent_message = await self.delete_torrents(data, client)
                    if torrent_message.status_code == 500:
                        return ResponseModel(
                            status_code=500,
                            status=False,
                            msg_en=f"Deleted rule for {data.official_title}, "
                            "but deleting its torrents failed.",
                            msg_zh=f"已删除 {data.official_title} 规则，但删除种子失败。",
                        )
            logger.info(f"Delete rule for {data.official_title}")
            return ResponseModel(
                status_code=200,
                status=True,
                msg_en=f"Delete rule for {data.official_title}. {torrent_message.msg_en if file and torrent_message else ''}",
                msg_zh=f"删除 {data.official_title} 规则。{torrent_message.msg_zh if file and torrent_message else ''}",
            )
        else:
            return ResponseModel(
                status_code=406,
                status=False,
                msg_en=f"Can't find id {_id}",
                msg_zh=f"无法找到 id {_id}",
            )

    async def disable_rule(self, _id: str | int, file: bool = False):
        data = await self.db.bangumi.search_id(int(_id))
        if isinstance(data, Bangumi):
            data.deleted = True
            await self.db.bangumi.update(data)
            if file:
                # Only the file-cleanup path needs the downloader, so an
                # unreachable downloader shouldn't block a DB-only disable.
                async with DownloadClient() as client:
                    return await self.delete_torrents(data, client)
            logger.info(f"Disable rule for {data.official_title}")
            return ResponseModel(
                status_code=200,
                status=True,
                msg_en=f"Disable rule for {data.official_title}",
                msg_zh=f"禁用 {data.official_title} 规则",
            )
        else:
            return ResponseModel(
                status_code=406,
                status=False,
                msg_en=f"Can't find id {_id}",
                msg_zh=f"无法找到 id {_id}",
            )

    async def enable_rule(self, _id: str | int):
        data = await self.db.bangumi.search_id(int(_id))
        if data:
            data.deleted = False
            await self.db.bangumi.update(data)
            logger.info(f"Enable rule for {data.official_title}")
            return ResponseModel(
                status_code=200,
                status=True,
                msg_en=f"Enable rule for {data.official_title}",
                msg_zh=f"启用 {data.official_title} 规则",
            )
        else:
            return ResponseModel(
                status_code=406,
                status=False,
                msg_en=f"Can't find id {_id}",
                msg_zh=f"无法找到 id {_id}",
            )

    async def update_rule(self, bangumi_id, data: BangumiUpdate):
        old_data = await self.db.bangumi.search_id(bangumi_id)
        if old_data:
            # Move torrent
            match_list = await self.__match_torrents_list(old_data)
            async with DownloadClient() as client:
                new_path = gen_save_path(data)
                old_path = old_data.save_path

                # Move existing torrents to new location if path changed
                if match_list and new_path != old_path:
                    await client.move_torrent(match_list, new_path)
                    logger.info(f"Moved torrents from {old_path} to {new_path}")

                # Update qBittorrent RSS rule if save_path changed
                if new_path != old_path and old_data.rule_name:
                    # Recreate the rule with the new save_path
                    rule = build_rss_rule(data, new_path)
                    await client.set_rss_rule(old_data.rule_name, rule)
                    logger.info(
                        f"Updated RSS rule {old_data.rule_name} with new save_path"
                    )

            data.save_path = new_path
            await self.db.bangumi.update(data, bangumi_id)
            return ResponseModel(
                status_code=200,
                status=True,
                msg_en=f"Update rule for {data.official_title}",
                msg_zh=f"更新 {data.official_title} 规则",
            )
        else:
            logger.error(f"Can't find data with {bangumi_id}")
            return ResponseModel(
                status_code=406,
                status=False,
                msg_en=f"Can't find data with {bangumi_id}",
                msg_zh=f"无法找到 id {bangumi_id} 的数据",
            )

    async def refresh_poster(self):
        bangumis = await self.db.bangumi.search_all()
        for bangumi in bangumis:
            if not bangumi.poster_link:
                await TitleParser().tmdb_poster_parser(bangumi)
        await self.db.bangumi.update_all(bangumis)
        return ResponseModel(
            status_code=200,
            status=True,
            msg_en="Refresh poster link successfully.",
            msg_zh="刷新海报链接成功。",
        )

    async def refind_poster(self, bangumi_id: int):
        bangumi = await self.db.bangumi.search_id(bangumi_id)
        if not bangumi:
            return ResponseModel(
                status_code=406,
                status=False,
                msg_en=f"Can't find id {bangumi_id}",
                msg_zh=f"无法找到 id {bangumi_id}",
            )
        await TitleParser().tmdb_poster_parser(bangumi)
        await self.db.bangumi.update(bangumi)
        return ResponseModel(
            status_code=200,
            status=True,
            msg_en="Refresh poster link successfully.",
            msg_zh="刷新海报链接成功。",
        )

    async def _weekdays_from_bgm(self, targets: list[Bangumi]) -> dict[int, int]:
        """One /calendar request, then pure in-memory title matching."""
        calendar_items = await fetch_bgm_calendar()
        if not calendar_items:
            logger.warning("Calendar refresh: Bangumi.tv returned no data.")
            return {}
        resolved = {}
        for bangumi in targets:
            weekday = match_weekday(
                bangumi.official_title, bangumi.title_raw, calendar_items
            )
            if weekday is not None:
                resolved[bangumi.id] = weekday
        return resolved

    async def _weekdays_from_parsers(self, targets: list[Bangumi]) -> dict[int, int]:
        """One lookup per bangumi, following each subscription's parser type.

        Archived anime are skipped here but not on the bgm path: they no longer
        appear on the calendar (#1077), so spending a Mikan/TMDB request each is
        pure waste — whereas the bgm request is already paid for and its
        matching is free, and skipping there would show a stale weekday between
        un-archiving and the next tick.
        """
        candidates = [b for b in targets if not b.archived]
        if not candidates:
            return {}
        rss_items = await self.db.rss.search_all()
        rss_by_id = {item.id: item for item in rss_items}
        rss_by_url = {item.url: item for item in rss_items}
        homepages = await self.db.torrent.search_homepages_by_bangumi_ids(
            [b.id for b in candidates]
        )
        semaphore = asyncio.Semaphore(CALENDAR_PARSER_CONCURRENCY)

        def parser_of(bangumi: Bangumi, rss_id: int | None) -> str | None:
            # Torrent.rss_id is a real foreign key, so prefer it; Bangumi.rss_link
            # is a plain string and search subscriptions store a RSS/Search URL
            # that is not in the rssitem table at all.
            item = (
                rss_by_id.get(rss_id) if rss_id is not None else None
            ) or rss_by_url.get(bangumi.rss_link)
            if item is not None:
                return item.parser
            if bangumi.rss_link:
                return "mikan" if "mikan" in urlparse(bangumi.rss_link).netloc else None
            return None

        async def resolve(bangumi: Bangumi) -> tuple[int, int | None]:
            rss_id, homepage = homepages.get(bangumi.id, (None, None))
            async with semaphore:
                return bangumi.id, await parser_weekday(
                    parser=parser_of(bangumi, rss_id),
                    official_title=bangumi.official_title,
                    episode_type=bangumi.episode_type,
                    episode_homepage=homepage,
                )

        results = await asyncio.gather(
            *(resolve(b) for b in candidates), return_exceptions=True
        )
        resolved = {}
        for result in results:
            if isinstance(result, BaseException):
                logger.warning("Calendar refresh: parser lookup failed: %s", result)
                continue
            bangumi_id, weekday = result
            if weekday is not None:
                resolved[bangumi_id] = weekday
        return resolved

    async def refresh_calendar(self):
        """Refresh air_weekday for every bangumi that isn't manually locked.

        The configured source is asked first; whatever it cannot resolve falls
        through to the other one. With the default bgm-first setting and a full
        match, the parser path never fires a single request.
        """
        # Read once: a config reload mid-refresh must not flip primary/fallback
        primary = settings.rss_parser.weekday_source
        sources = {
            "bgm": self._weekdays_from_bgm,
            "parser": self._weekdays_from_parsers,
        }
        fallback = "parser" if primary == "bgm" else "bgm"

        bangumis = await self.db.bangumi.search_all()
        targets = [b for b in bangumis if not b.deleted and not b.weekday_locked]
        if not targets:
            return ResponseModel(
                status_code=200,
                status=True,
                msg_en="No bangumi needs a calendar refresh.",
                msg_zh="没有需要刷新放送表的番剧。",
            )

        resolved = await sources[primary](targets)
        counts = {primary: len(resolved), fallback: 0}
        remaining = [b for b in targets if b.id not in resolved]
        if remaining:
            extra = await sources[fallback](remaining)
            counts[fallback] = len(extra)
            resolved.update(extra)

        changed = []
        for bangumi in targets:
            weekday = resolved.get(bangumi.id)
            if weekday is not None and weekday != bangumi.air_weekday:
                bangumi.air_weekday = weekday
                changed.append(bangumi)
        if changed:
            await self.db.bangumi.update_all(changed)

        unresolved = len(targets) - len(resolved)
        logger.info(
            "Calendar refresh (%s first): updated %s, resolved by parser %s / "
            "bgm.tv %s, %s unresolved.",
            primary,
            len(changed),
            counts["parser"],
            counts["bgm"],
            unresolved,
        )
        if not resolved:
            # Only a total failure is an error: a partial result still moved the
            # calendar forward, and reporting 500 would make calendar_tick log a
            # daily "refresh failed" for users whose fallback source is blocked.
            return ResponseModel(
                status_code=500,
                status=False,
                msg_en="Failed to resolve any air weekday from Bangumi.tv or the parsers.",
                msg_zh="未能从 Bangumi.tv 或解析器获取到任何放送星期。",
            )
        return ResponseModel(
            status_code=200,
            status=True,
            msg_en=(
                f"Calendar refreshed. Updated {len(changed)} anime "
                f"(parser {counts['parser']}, Bangumi.tv {counts['bgm']}); "
                f"{unresolved} unresolved."
            ),
            msg_zh=(
                f"放送表已刷新，更新了 {len(changed)} 部番剧"
                f"（解析器 {counts['parser']} 部、Bangumi.tv {counts['bgm']} 部），"
                f"{unresolved} 部未匹配。"
            ),
        )

    async def search_all_bangumi(self):
        datas = await self.db.bangumi.search_all()
        if not datas:
            return []
        return [data for data in datas if not data.deleted]

    async def search_one(self, _id: int | str):
        data = await self.db.bangumi.search_id(int(_id))
        if not data:
            logger.error(f"Can't find data with {_id}")
            return ResponseModel(
                status_code=406,
                status=False,
                msg_en=f"Can't find data with {_id}",
                msg_zh=f"无法找到 id {_id} 的数据",
            )
        else:
            return data

    async def archive_rule(self, _id: int):
        """Archive a bangumi."""
        data = await self.db.bangumi.search_id(_id)
        if not data:
            return ResponseModel(
                status_code=406,
                status=False,
                msg_en=f"Can't find id {_id}",
                msg_zh=f"无法找到 id {_id}",
            )
        if await self.db.bangumi.archive_one(_id):
            logger.info(f"Archived {data.official_title}")
            return ResponseModel(
                status_code=200,
                status=True,
                msg_en=f"Archived {data.official_title}",
                msg_zh=f"已归档 {data.official_title}",
            )
        return ResponseModel(
            status_code=500,
            status=False,
            msg_en=f"Failed to archive {data.official_title}",
            msg_zh=f"归档 {data.official_title} 失败",
        )

    async def unarchive_rule(self, _id: int):
        """Unarchive a bangumi."""
        data = await self.db.bangumi.search_id(_id)
        if not data:
            return ResponseModel(
                status_code=406,
                status=False,
                msg_en=f"Can't find id {_id}",
                msg_zh=f"无法找到 id {_id}",
            )
        if await self.db.bangumi.unarchive_one(_id):
            logger.info(f"Unarchived {data.official_title}")
            return ResponseModel(
                status_code=200,
                status=True,
                msg_en=f"Unarchived {data.official_title}",
                msg_zh=f"已取消归档 {data.official_title}",
            )
        return ResponseModel(
            status_code=500,
            status=False,
            msg_en=f"Failed to unarchive {data.official_title}",
            msg_zh=f"取消归档 {data.official_title} 失败",
        )

    async def refresh_metadata(self):
        """Refresh TMDB metadata and auto-archive ended series."""
        bangumis = await self.db.bangumi.search_all()
        language = settings.rss_parser.language
        archived_count = 0
        poster_count = 0

        for bangumi in bangumis:
            if bangumi.deleted:
                continue
            tmdb_info = await tmdb_parser(bangumi.official_title, language)
            if tmdb_info:
                # Update poster if missing
                if not bangumi.poster_link and tmdb_info.poster_link:
                    bangumi.poster_link = tmdb_info.poster_link
                    poster_count += 1
                # Auto-archive ended series
                if tmdb_info.series_status == "Ended" and not bangumi.archived:
                    bangumi.archived = True
                    archived_count += 1
                    logger.info(f"Auto-archived ended series: {bangumi.official_title}")

        if archived_count > 0 or poster_count > 0:
            await self.db.bangumi.update_all(bangumis)

        logger.info(
            f"Metadata refresh: archived {archived_count}, updated posters {poster_count}"
        )
        return ResponseModel(
            status_code=200,
            status=True,
            msg_en=f"Metadata refreshed. Archived {archived_count} ended series, updated {poster_count} posters.",
            msg_zh=f"已刷新元数据。归档了 {archived_count} 部已完结番剧，更新了 {poster_count} 个海报。",
        )

    async def suggest_offset(self, bangumi_id: int) -> dict:
        """Suggest offset based on TMDB episode counts."""
        data = await self.db.bangumi.search_id(bangumi_id)
        if not data:
            return {
                "suggested_offset": 0,
                "reason": f"Bangumi id {bangumi_id} not found",
            }

        language = settings.rss_parser.language
        tmdb_info = await tmdb_parser(data.official_title, language)

        if not tmdb_info or not tmdb_info.season_episode_counts:
            return {
                "suggested_offset": 0,
                "reason": "Unable to fetch TMDB episode data",
            }

        season = data.season
        if season <= 1:
            return {"suggested_offset": 0, "reason": "Season 1 does not need offset"}

        offset = tmdb_info.get_offset_for_season(season)
        if offset == 0:
            return {"suggested_offset": 0, "reason": "No previous seasons found"}

        # Build reason with episode counts
        prev_seasons = [
            f"S{s}: {tmdb_info.season_episode_counts.get(s, 0)} eps"
            for s in range(1, season)
            if s in tmdb_info.season_episode_counts
        ]
        reason = f"Previous seasons: {', '.join(prev_seasons)}"

        return {"suggested_offset": offset, "reason": reason}
