"""mix 来源解析器：用 Mikan 的官方名与放送开始日期去查 TMDB。

单独的 mikan 解析器名字准但给不出年份和季号；单独的 tmdb 解析器有年份和
季号，但搜索词来自种子名（简写、别名、带空格的中文名经常搜不中），且同名
动画有多个时无脑取第一个。mix 把两者接起来：

1. 从 Mikan 剧集主页取官方名（搜索词质量大幅提升）与放送开始日期；
2. 用官方名 + 日期查 TMDB，日期负责在多个动画候选里定位到正确的那部与那一季。

任何一环拿不到都逐级降级，绝不比单用 mikan 或 tmdb 更差：

- 没有 homepage（非 Mikan 源）        → 用标题解析器的名字查 TMDB，等同纯 tmdb
- Mikan 抓页失败（AttributeError）    → 同上
- Mikan 成功但没有放送开始日期        → 用 Mikan 官方名查 TMDB，不带日期
- TMDB 未命中                         → 退回 Mikan 的官方名与海报
"""

import logging
from dataclasses import dataclass

from .mikan_parser import MikanInfo, mikan_parser
from .tmdb_parser import tmdb_parser

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MixResult:
    """mix 解析的产出。字段为 ``None`` 表示「没拿到，不要覆盖调用方的值」。"""

    official_title: str | None = None
    year: str | None = None
    season: int | None = None
    poster_link: str | None = None
    air_weekday: int | None = None


async def _fetch_mikan(homepage: str | None) -> MikanInfo | None:
    if not homepage:
        return None
    try:
        return await mikan_parser(homepage)
    except AttributeError as e:
        # mikan_parser 用 AttributeError 表示「页面结构不对/抓取失败」，
        # 见该模块里关于「故意不做 None 防御」的注释。
        logger.warning("Failed to parse Mikan homepage %s: %s", homepage, e)
        return None


async def mix_parser(
    homepage: str | None,
    fallback_title: str,
    language: str,
    *,
    is_movie: bool = False,
) -> MixResult:
    """按 mix 策略解析，返回要写回番剧/剧场版的字段。

    Args:
        homepage: 种子对应的 Mikan 剧集主页；非 Mikan 源为空。
        fallback_title: 标题解析器从种子名里得到的名字，Mikan 不可用时的搜索词。
        language: ``rss_parser.language``，同时决定 TMDB 的查询语言。
        is_movie: 是否按剧场版/电影查询（走 search/movie，且不返回季号）。
    """
    mikan_info = await _fetch_mikan(homepage)
    mikan_title = mikan_info.official_title if mikan_info else None
    mikan_poster = mikan_info.poster_link if mikan_info else None
    air_date = mikan_info.air_date if mikan_info else None
    mikan_weekday = mikan_info.air_weekday if mikan_info else None

    query_title = mikan_title or fallback_title
    if not query_title:
        return MixResult()

    tmdb_info = await tmdb_parser(
        query_title, language, is_movie=is_movie, air_date=air_date
    )
    if tmdb_info is None:
        # TMDB 上没有对应条目（含日期全部超出选番窗口）：退回 Mikan 的结果。
        logger.debug("TMDB has no match for '%s'; keeping Mikan data", query_title)
        return MixResult(
            official_title=mikan_title or None,
            poster_link=mikan_poster or None,
            air_weekday=mikan_weekday,
        )

    # 剧场版没有季度概念；剧集优先用日期匹配到的季号，其次是 TMDB 的季数。
    # 两者都拿不到时返回 None，由调用方保留标题解析出的季号。
    season = None
    if not is_movie:
        season = tmdb_info.matched_season or tmdb_info.last_season or None

    return MixResult(
        official_title=tmdb_info.title or mikan_title or None,
        year=tmdb_info.year or None,
        season=season,
        # 海报优先用 TMDB 的，TMDB 没有再退回 Mikan 抓到的
        poster_link=tmdb_info.poster_link or mikan_poster or None,
        # 星期反过来：Mikan 主页上明写的是站点权威值，TMDB 那边是推算的
        air_weekday=(
            mikan_weekday if mikan_weekday is not None else tmdb_info.air_weekday
        ),
    )
