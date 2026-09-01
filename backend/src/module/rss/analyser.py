import logging
import re

from module.conf import settings
from module.models import Bangumi, Movie, ResponseModel, RSSItem, Torrent
from module.network import RequestContent
from module.parser import TitleParser
from module.parser.analyser import mix_parser
from module.parser.analyser.selector import parser_engine_snapshot
from module.parser.analyser.weekday_source import parser_weekday

from .engine import RSSEngine

logger = logging.getLogger(__name__)


def apply_default_preferences(bangumi: Bangumi) -> None:
    """给新解析出的番剧套上「解析设置」里的全局默认偏好。

    默认值只在番剧首次入库这一刻生效：写进番剧行之后就归该行所有，之后改
    全局设置不会回头改它——和 ``group_name`` / ``subtitle`` 这些解析出来就
    固化的字段一个道理，避免用户逐番调好的偏好被一次全局改动冲掉。

    只填空字段，调用方（如搜索页预览）已经定好的值优先。空字符串统一写成
    ``None``，让「没设置」在库里只有 NULL 一种表示。全局默认全留空时这个
    函数什么都不做，行为与升级前完全一致。
    """
    defaults = settings.rss_parser
    if not bangumi.preferred_group:
        bangumi.preferred_group = defaults.default_preferred_group or None
    if not bangumi.preferred_resolution:
        bangumi.preferred_resolution = defaults.default_preferred_resolution or None
    if not bangumi.preferred_subtitle_language:
        bangumi.preferred_subtitle_language = (
            defaults.default_preferred_subtitle_language or None
        )
    if not bangumi.preferred_subtitle_style:
        bangumi.preferred_subtitle_style = (
            defaults.default_preferred_subtitle_style or None
        )


class RSSAnalyser:
    async def official_title_parser_movie(
        self,
        movie: Movie,
        rss: RSSItem,
        torrent: Torrent,
        fetch_poster: bool = True,
    ):
        if not fetch_poster:
            pass
        elif rss.parser == "mikan":
            if not torrent.homepage:
                logger.warning("Mikan movie torrent has no homepage info.")
            else:
                try:
                    mikan_info = await TitleParser.mikan_parser(torrent.homepage)
                except AttributeError as e:
                    logger.warning(
                        f"Failed to parse Mikan homepage {torrent.homepage}: {e}"
                    )
                else:
                    movie.poster_link = mikan_info.poster_link
                    if mikan_info.official_title:
                        movie.official_title = mikan_info.official_title
        elif rss.parser == "tmdb":
            tmdb_title, _, year, poster_link = await TitleParser.tmdb_parser(
                movie.official_title,
                1,
                settings.rss_parser.language,
                episode_type="movie",
            )
            movie.official_title = tmdb_title
            if year:
                try:
                    movie.year = int(year)
                except (ValueError, TypeError):
                    pass
            movie.poster_link = poster_link
        elif rss.parser == "mix":
            # mix 自己处理全部降级，空字段一律表示「没拿到，别覆盖」
            mix_result = await mix_parser(
                torrent.homepage,
                movie.official_title,
                settings.rss_parser.language,
                is_movie=True,
            )
            if mix_result.official_title:
                movie.official_title = mix_result.official_title
            if mix_result.year:
                try:
                    movie.year = int(mix_result.year)
                except (ValueError, TypeError):
                    pass
            if mix_result.poster_link:
                movie.poster_link = mix_result.poster_link
        if movie.official_title:
            movie.official_title = re.sub(r"[/:.\\]", " ", movie.official_title)

    async def official_title_parser(
        self,
        bangumi: Bangumi,
        rss: RSSItem,
        torrent: Torrent,
        fetch_poster: bool = True,
    ):
        # 放送星期只在「优先解析器」模式下顺手写入，且只写解析器手上已有的值：
        # 入库路径不回退 bgm.tv，拿不到就留空，交给日历的定时刷新去补。
        want_weekday = (
            fetch_poster and settings.rss_parser.weekday_source == "parser"
        ) and bangumi.episode_type != "movie"
        if not fetch_poster:
            pass
        elif rss.parser == "mikan":
            if not torrent.homepage:
                logger.warning("Mikan torrent has no homepage info.")
            else:
                try:
                    mikan_info = await TitleParser.mikan_parser(torrent.homepage)
                except AttributeError as e:
                    logger.warning(
                        f"Failed to parse Mikan homepage " f"{torrent.homepage}: {e}"
                    )
                else:
                    bangumi.poster_link = mikan_info.poster_link
                    if mikan_info.official_title:
                        bangumi.official_title = mikan_info.official_title
                    if want_weekday and mikan_info.air_weekday is not None:
                        bangumi.air_weekday = mikan_info.air_weekday
        elif rss.parser == "tmdb":
            # TMDB 缓存以查询词为键，而下面会把 official_title 覆盖成 TMDB 的
            # 标准名——先留住原查询词，星期查询才能命中缓存、不多发一次请求
            query_title = bangumi.official_title
            tmdb_title, season, year, poster_link = await TitleParser.tmdb_parser(
                bangumi.official_title,
                bangumi.season,
                settings.rss_parser.language,
                episode_type=bangumi.episode_type,
            )
            bangumi.official_title = tmdb_title
            bangumi.year = year
            bangumi.season = season
            bangumi.poster_link = poster_link
            if want_weekday:
                weekday = await parser_weekday(
                    parser=rss.parser,
                    official_title=query_title,
                    episode_type=bangumi.episode_type,
                )
                if weekday is not None:
                    bangumi.air_weekday = weekday
        elif rss.parser == "mix":
            # mix 自己处理全部降级，空字段一律表示「没拿到，别覆盖」
            mix_result = await mix_parser(
                torrent.homepage,
                bangumi.official_title,
                settings.rss_parser.language,
                is_movie=bangumi.episode_type == "movie",
            )
            if mix_result.official_title:
                bangumi.official_title = mix_result.official_title
            if mix_result.year:
                bangumi.year = mix_result.year
            if mix_result.season:
                bangumi.season = mix_result.season
            if mix_result.poster_link:
                bangumi.poster_link = mix_result.poster_link
            if want_weekday and mix_result.air_weekday is not None:
                bangumi.air_weekday = mix_result.air_weekday
        else:
            pass
        if bangumi.official_title:
            bangumi.official_title = re.sub(r"[/:.\\]", " ", bangumi.official_title)

    @staticmethod
    async def get_rss_torrents(rss_link: str, full_parse: bool = True) -> list[Torrent]:
        async with RequestContent() as req:
            if full_parse:
                rss_torrents = await req.get_torrents(rss_link)
            else:
                rss_torrents = await req.get_torrents(rss_link, "\\d+-\\d+")
        return rss_torrents

    async def torrents_to_data(
        self, torrents: list[Torrent], rss: RSSItem, full_parse: bool = True
    ) -> tuple[list[Bangumi], list[Movie]]:
        new_bangumi: list[Bangumi] = []
        new_movies: list[Movie] = []
        seen_identities: set[tuple[str, str, int]] = set()
        for torrent in torrents:
            result = await TitleParser.raw_parser(raw=torrent.name)
            if result is None or not result.title_raw:
                continue
            title_raw = result.title_raw
            identity = (
                ("movie", title_raw, 0)
                if isinstance(result, Movie)
                else (result.episode_type, title_raw, result.season)
            )
            if identity in seen_identities:
                continue
            if isinstance(result, Movie):
                await self.official_title_parser_movie(
                    movie=result, rss=rss, torrent=torrent
                )
                result.rss_link = rss.url
                seen_identities.add(identity)
                new_movies.append(result)
                logger.info(f"New movie found: {result.official_title}")
            elif isinstance(result, Bangumi):
                await self.official_title_parser(
                    bangumi=result, rss=rss, torrent=torrent
                )
                result.rss_link = rss.url
                apply_default_preferences(result)
                if not full_parse:
                    return [result], new_movies
                seen_identities.add(identity)
                new_bangumi.append(result)
                logger.info(f"New bangumi found: {result.official_title}")
        return new_bangumi, new_movies

    async def torrent_to_data(
        self, torrent: Torrent, rss: RSSItem, fetch_poster: bool = True
    ) -> Bangumi | Movie | None:
        result = await TitleParser.raw_parser(raw=torrent.name)
        if result:
            if isinstance(result, Movie):
                await self.official_title_parser_movie(
                    movie=result,
                    rss=rss,
                    torrent=torrent,
                    fetch_poster=fetch_poster,
                )
                result.rss_link = rss.url
            else:
                await self.official_title_parser(
                    bangumi=result,
                    rss=rss,
                    torrent=torrent,
                    fetch_poster=fetch_poster,
                )
                result.rss_link = rss.url
                apply_default_preferences(result)
            return result
        return None

    async def rss_to_data(
        self, rss: RSSItem, engine: RSSEngine, full_parse: bool = True
    ) -> list[Bangumi]:
        # One RSS workflow can cross several await boundaries and parse the
        # same resource during Movie match, Bangumi match, and persistence.
        # Keep those stages on the engine selected when the workflow started.
        with parser_engine_snapshot():
            rss_torrents = await self.get_rss_torrents(rss.url, full_parse)
            # Filter out already-known movies first
            torrents_after_movies = await engine.db.movie.match_list(
                rss_torrents, rss.url
            )
            # Then filter out already-known bangumi
            torrents_to_add = await engine.db.bangumi.match_list(
                torrents_after_movies, rss.url
            )
            if not torrents_to_add:
                logger.debug("No new title has been found.")
                return []
            # Parse remaining torrents
            new_bangumi, new_movies = await self.torrents_to_data(
                torrents_to_add, rss, full_parse
            )
            for movie in new_movies:
                await engine.db.movie.add(movie)
            if new_bangumi:
                await engine.db.bangumi.add_all(new_bangumi)
                return new_bangumi
            return []

    async def link_to_data(self, rss: RSSItem) -> Bangumi | Movie | ResponseModel:
        torrents = await self.get_rss_torrents(rss.url, False)
        if not torrents:
            return ResponseModel(
                status=False,
                status_code=406,
                msg_en="Cannot find any torrent.",
                msg_zh="无法找到种子。",
            )
        for torrent in torrents:
            data = await self.torrent_to_data(torrent, rss)
            if data:
                return data
        return ResponseModel(
            status=False,
            status_code=406,
            msg_en="Cannot parse this link.",
            msg_zh="无法解析此链接。",
        )
