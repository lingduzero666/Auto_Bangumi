import datetime
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass

from bs4 import BeautifulSoup
from urllib3.util import parse_url

from module.network import RequestContent
from module.utils import save_image

logger = logging.getLogger(__name__)

# Mikan 服务端按 en-US 输出日期，格式是 M/D/YYYY（如「放送开始：4/3/2026」
# 表示 2026 年 4 月 3 日）。不绑定具体 CSS 选择器，直接在页面纯文本上匹配，
# 这样 Mikan 调整标记结构时也不会失效。
_AIR_DATE = re.compile(r"放送开始[：:]\s*(\d{1,2})/(\d{1,2})/(\d{4})")

# In-memory cache for Mikan homepage lookups. Keyed by per-episode homepage
# URL, so it is bounded (LRU-ish, oldest-evicted) rather than unlimited.
_MIKAN_CACHE_MAX = 512


@dataclass(frozen=True, slots=True)
class MikanInfo:
    """Mikan 剧集主页上可用的番剧信息。

    ``air_date`` 是可选增强：页面没有「放送开始」行、或日期非法时为 None，
    调用方据此决定是否降级（见 mix_parser）。
    """

    poster_link: str
    official_title: str
    air_date: datetime.date | None = None


_mikan_cache: "OrderedDict[str, MikanInfo]" = OrderedDict()


def reset_cache() -> None:
    """清空 Mikan 主页解析缓存。配置重载后必须调用，否则会继续返回旧配置下
    缓存的结果。"""
    _mikan_cache.clear()


def _cache_result(homepage: str, result: MikanInfo) -> MikanInfo:
    if len(_mikan_cache) >= _MIKAN_CACHE_MAX:
        _mikan_cache.popitem(last=False)
    _mikan_cache[homepage] = result
    return result


def _parse_air_date(text: str) -> datetime.date | None:
    """从页面文本里取「放送开始」日期，取不到返回 None。

    与下面海报/标题的解析不同，这里**绝不抛异常**：日期只是可选增强，而
    调用方的 ``except AttributeError`` 语义是「页面结构变了」，不该被一个
    缺失的日期触发。
    """
    match = _AIR_DATE.search(text)
    if match is None:
        return None
    month, day, year = (int(group) for group in match.groups())
    try:
        return datetime.date(year, month, day)
    except ValueError:
        logger.debug("Ignoring invalid Mikan air date: %s", match.group())
        return None


async def mikan_parser(homepage: str) -> MikanInfo:
    if homepage in _mikan_cache:
        return _mikan_cache[homepage]
    root_path = parse_url(homepage).host
    async with RequestContent() as req:
        content = await req.get_html(homepage)
        # get_html returns None on a failed fetch; feed BeautifulSoup an empty
        # string instead of None so a network failure surfaces as a normal
        # "element not found" AttributeError rather than an uncaught TypeError.
        soup = BeautifulSoup(content or "", "html.parser")
        # .find()/.select_one() can return None (missing element); accessing
        # .get()/.text unguarded is deliberate -- see comment above -- so the
        # caller's `except AttributeError` catches a parse/network failure.
        poster_div = soup.find("div", {"class": "bangumi-poster"}).get(  # type: ignore[union-attr]
            "style"
        )
        official_title = soup.select_one(
            'p.bangumi-title a[href^="/Home/Bangumi/"]'
        ).text  # type: ignore[union-attr]
        official_title = re.sub(r"第.*季", "", official_title).strip()
        air_date = _parse_air_date(soup.get_text())
        if poster_div:
            # bs4's Tag.get() return type also covers multi-valued attrs
            # (AttributeValueList); "style" is never multi-valued in practice.
            poster_path = poster_div.split("url('")[1].split("')")[0]  # type: ignore[union-attr]
            poster_path = poster_path.split("?")[0]
            poster_url = f"https://{root_path}{poster_path}"
            img = await req.get_content(poster_url)
            suffix = poster_path.split(".")[-1]
            # img can be None if the poster download failed; don't crash on it.
            poster_link = (
                (await save_image(img, suffix, source_url=poster_url) or "")
                if img
                else ""
            )
            return _cache_result(
                homepage, MikanInfo(poster_link, official_title, air_date)
            )
        return _cache_result(homepage, MikanInfo("", official_title, air_date))


if __name__ == "__main__":
    import asyncio

    homepage = (
        "https://mikanani.me/Home/Episode/c89b3c6f0c1c0567a618f5288b853823c87a9862"
    )
    print(asyncio.run(mikan_parser(homepage)))
