"""放送星期的解析器来源：按订阅自己的解析类型取数。

日历的放送星期有两个来源。bgm.tv 是批量的（一次 /calendar 覆盖全部番剧，再
按标题模糊匹配），实现在 ``bgm_calendar``；解析器是逐条的，实现在这里：

- ``mikan`` / ``mix``：读 Mikan 番剧主页上明写的「放送日期：星期X」，站点权威值
- ``tmdb``：由 TMDB 已经拉回来的播出日期推算
- 其余解析类型（``parser`` 等）：没有解析器来源，交给 bgm 兜底

两条来源在 ``TorrentManager.refresh_calendar`` 里按配置的优先级编排、互为兜底。
本模块只做「问一个来源要一个番剧的星期」，不碰数据库、不产生 ResponseModel，
任何失败都返回 None 而不抛异常——调用方是批量任务，一部番的问题不该影响其余。
"""

import logging

from module.conf import settings

from .mikan_parser import mikan_weekday
from .tmdb_parser import tmdb_parser

logger = logging.getLogger(__name__)

# 走 Mikan 番剧主页的解析类型。mix 内部也是先抓 Mikan 主页，所以同样适用。
_MIKAN_PARSERS = frozenset({"mikan", "mix"})


async def parser_weekday(
    *,
    parser: str | None,
    official_title: str,
    episode_type: str = "episode",
    episode_homepage: str | None = None,
) -> int | None:
    """按订阅的解析类型取放送星期，返回 0=周一 .. 6=周日，取不到返回 None。

    Args:
        parser: ``RSSItem.parser``。
        official_title: 番剧名，TMDB 的查询词。
        episode_type: ``movie`` 直接放弃——剧场版没有每周档期。
        episode_homepage: Mikan 剧集页 URL（``Torrent.homepage``），只有 Mikan
            源的种子有；非 Mikan 源恒为空。
    """
    if episode_type == "movie":
        return None
    try:
        if parser in _MIKAN_PARSERS:
            if not episode_homepage:
                # 常见而非异常：老数据、从未下载过的番、非 Mikan 源都会走到这里
                logger.debug("No Mikan homepage for %s", official_title)
                return None
            return await mikan_weekday(episode_homepage)
        if parser == "tmdb":
            if not official_title:
                return None
            info = await tmdb_parser(official_title, settings.rss_parser.language)
            return info.air_weekday if info else None
    except Exception as e:  # noqa: BLE001 - 批量任务里单条失败必须降级而不是中断
        logger.debug("Parser weekday lookup failed for %s: %s", official_title, e)
        return None
    return None
