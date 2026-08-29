import datetime
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag
from urllib3.util import parse_url

from module.network import RequestContent
from module.utils import save_image

logger = logging.getLogger(__name__)

# 放送信息（放送开始 / 放送日期）只在**番剧主页** /Home/Bangumi/{id} 上；
# 剧集页 /Home/Episode/{hash} 的 bangumi-info 只有「发布日期」和「文件大小」。
# 两个正则都在主页纯文本上匹配，不绑定具体 CSS 选择器，这样 Mikan 调整标记
# 结构时也不会失效。
#
# 日期按 en-US 输出，格式是 M/D/YYYY（「放送开始：4/3/2026」= 2026 年 4 月 3 日）。
_AIR_DATE = re.compile(r"放送开始[：:]\s*(\d{1,2})/(\d{1,2})/(\d{4})")
# 星期只认页面上明写的「放送日期：星期X」。**不要**从 air_date 反推：M/D/YYYY
# 与 D/M/YYYY 无法区分，反推会静默写错星期。选番时日期差一个月无所谓（见
# tmdb_parser._CANDIDATE_WINDOW_DAYS），但星期差一天就是错的。
_AIR_WEEKDAY = re.compile(r"放送日期[：:]\s*(?:星期|周|週)\s*([一二三四五六日天])")
_WEEKDAY_ZH = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}

# In-memory caches for Mikan lookups, both bounded (LRU-ish, oldest-evicted).
# `_mikan_cache` is keyed by per-episode homepage URL; `_bangumi_page_cache` by
# bangumi homepage URL, because one anime has N subtitle groups x M episodes
# worth of episode pages all pointing at the same bangumi page — without the
# second cache that page would be refetched N*M times.
_MIKAN_CACHE_MAX = 512


@dataclass(frozen=True, slots=True)
class MikanInfo:
    """Mikan 上可用的番剧信息。

    ``poster_link`` / ``official_title`` 来自剧集页；``air_date`` /
    ``air_weekday`` 来自剧集页链过去的番剧主页。后两者都是可选增强：主页抓
    不到、页面没有对应行、或日期非法时为 None，调用方据此决定是否降级
    （见 mix_parser 与 weekday_source）。
    """

    poster_link: str
    official_title: str
    air_date: datetime.date | None = None
    air_weekday: int | None = None


_mikan_cache: "OrderedDict[str, MikanInfo]" = OrderedDict()
# 番剧主页 URL -> (放送开始日期, 放送星期)
_bangumi_page_cache: "OrderedDict[str, tuple[datetime.date | None, int | None]]" = (
    OrderedDict()
)


def reset_cache() -> None:
    """清空 Mikan 解析缓存（剧集页与番剧主页两层）。配置重载后必须调用，
    否则会继续返回旧配置下缓存的结果。"""
    _mikan_cache.clear()
    _bangumi_page_cache.clear()


def _cache_result(homepage: str, result: MikanInfo) -> MikanInfo:
    if len(_mikan_cache) >= _MIKAN_CACHE_MAX:
        _mikan_cache.popitem(last=False)
    _mikan_cache[homepage] = result
    return result


def _cache_bangumi_page(
    url: str, result: tuple[datetime.date | None, int | None]
) -> tuple[datetime.date | None, int | None]:
    if len(_bangumi_page_cache) >= _MIKAN_CACHE_MAX:
        _bangumi_page_cache.popitem(last=False)
    _bangumi_page_cache[url] = result
    return result


def _parse_air_date(text: str) -> datetime.date | None:
    """从番剧主页文本里取「放送开始」日期，取不到返回 None。

    与海报/标题的解析不同，这里**绝不抛异常**：日期只是可选增强，而调用方的
    ``except AttributeError`` 语义是「页面结构变了」，不该被一个缺失的日期
    触发。``_parse_air_weekday`` 同理。
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


def _parse_air_weekday(text: str) -> int | None:
    """从番剧主页文本里取「放送日期：星期X」，返回 0=周一 .. 6=周日。

    与 bgm_calendar / Bangumi.air_weekday 的约定一致。未知字样（如「不定期」）
    与缺失行都返回 None。
    """
    match = _AIR_WEEKDAY.search(text)
    if match is None:
        return None
    return _WEEKDAY_ZH.get(match.group(1))


def _bangumi_page_url(episode_homepage: str, anchor: Tag | None) -> str | None:
    """由剧集页上的番剧链接拼出番剧主页的绝对 URL。

    href 形如 ``/Home/Bangumi/3060#203``，``#203`` 是字幕组锚点，必须去掉：
    否则同一部番的每个字幕组都会得到一个不同的缓存键，主页去重完全失效。
    host 取自传入的剧集页 URL 而不是硬编码域名，镜像站（mikanime.tv 等）才
    能正常工作。
    """
    href = anchor.get("href") if anchor is not None else None
    # bs4 的 Tag.get() 也覆盖多值属性（AttributeValueList），窄化成 str 才安全
    if not isinstance(href, str):
        return None
    host = parse_url(episode_homepage).host
    if not host:
        return None
    scheme = parse_url(episode_homepage).scheme or "https"
    return f"{scheme}://{host}{href.split('#')[0]}"


async def _fetch_bangumi_page(
    url: str, req: RequestContent
) -> tuple[datetime.date | None, int | None]:
    """抓番剧主页并取出放送开始日期与放送星期。

    整段吞掉异常：主页只承载可选增强，抓取失败不该让调用方以为剧集页的结构
    变了（那是 ``AttributeError`` 的语义）。失败结果**不入缓存**——模块级缓存
    活到进程结束，把 None 永久缓存会让 Mikan 后来补上的放送信息一辈子取不到。
    """
    if url in _bangumi_page_cache:
        _bangumi_page_cache.move_to_end(url)
        return _bangumi_page_cache[url]
    try:
        content = await req.get_html(url)
    except Exception as e:  # noqa: BLE001 - 可选增强，任何网络/解析异常都降级
        logger.debug("Failed to fetch Mikan bangumi page %s: %s", url, e)
        return None, None
    if not content:
        return None, None
    text = BeautifulSoup(content, "html.parser").get_text()
    result = (_parse_air_date(text), _parse_air_weekday(text))
    if result == (None, None):
        return result
    return _cache_bangumi_page(url, result)


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
        title_anchor = soup.select_one('p.bangumi-title a[href^="/Home/Bangumi/"]')
        official_title = title_anchor.text  # type: ignore[union-attr]
        official_title = re.sub(r"第.*季", "", official_title).strip()
        # 放送信息在剧集页链过去的番剧主页上，剧集页本身没有
        bangumi_url = _bangumi_page_url(homepage, title_anchor)
        air_date, air_weekday = (
            await _fetch_bangumi_page(bangumi_url, req) if bangumi_url else (None, None)
        )
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
                homepage,
                MikanInfo(poster_link, official_title, air_date, air_weekday),
            )
        return _cache_result(
            homepage, MikanInfo("", official_title, air_date, air_weekday)
        )


if __name__ == "__main__":
    import asyncio

    homepage = (
        "https://mikanani.me/Home/Episode/c89b3c6f0c1c0567a618f5288b853823c87a9862"
    )
    print(asyncio.run(mikan_parser(homepage)))
