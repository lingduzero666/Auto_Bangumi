import asyncio
import datetime
import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from urllib.parse import urlencode

from module.conf import TMDB_API, settings
from module.network import RequestContent
from module.utils import save_image

logger = logging.getLogger(__name__)


def _tmdb_url() -> str:
    # Read live so a config change (e.g. a GFW mirror, #1042) takes effect
    # without a restart.
    return settings.network.tmdb_base_url.rstrip("/")


def _api_key() -> str:
    # 用户自配 key 优先（#975）；留空回退到内置共享 key。同样读实时值
    return settings.network.tmdb_api_key or TMDB_API


# 传入 air_date 时用于「选番」的窗口（天）。放得宽是必须的：分割放送
# （split-cour）在 TMDB 上是**一个** season，seasons[].air_date 只记第一
# cour 的日期，而 Mikan 会把第二 cour 列成独立条目（放送开始晚约半年）。
# 选出番之后再用集级 air_date 精确定季，见 _match_season_by_episodes()。
_CANDIDATE_WINDOW_DAYS = 200

# 传入 air_date 时最多检查多少个搜索候选。TMDB 搜索结果按相关度排序，
# 第 5 名之后基本是噪声；每个候选都要一次详情请求，不设上限会在自建
# tmdb_base_url 镜像上打满连接池并触发限流。
_AIR_DATE_MAX_CANDIDATES = 5

# In-memory cache for TMDB lookups to avoid repeated API calls
_TMDB_CACHE_MAX = 512
_tmdb_cache: OrderedDict[str, "TMDBInfo | None"] = OrderedDict()


def reset_cache() -> None:
    """清空 TMDB 查询缓存。配置重载（如 tmdb_base_url 变更）后必须调用，否则会
    继续返回旧接口地址下缓存的结果。"""
    _tmdb_cache.clear()


@dataclass
class TMDBInfo:
    id: int
    title: str
    original_title: str
    season: list[dict]
    last_season: int
    year: str
    poster_link: str | None = None
    series_status: str | None = None  # "Ended", "Returning Series", etc.
    season_episode_counts: dict[int, int] | None = None  # {1: 13, 2: 12, ...}
    virtual_season_starts: dict[int, list[int]] | None = (
        None  # {1: [1, 29], ...} - episode numbers where virtual seasons start
    )
    # 传入 air_date 时匹配到的季号。**不要用 last_season 承载它**：
    # last_season 的语义是「TMDB 上一共有几季」，offset_detector 拿它做
    # `parsed_season > last_season` 的越界判断、文案也写着「TMDB只有N季」，
    # 覆盖成匹配季号会让合法的 S2 订阅误报 offset 建议。
    matched_season: int | None = None
    # 放送星期，0=周一 .. 6=周日，与 bgm_calendar / Bangumi.air_weekday 一致。
    # 全部由本模块**已经拉过**的响应推导，不产生额外请求，见 _derive_air_weekday。
    air_weekday: int | None = None

    def get_offset_for_season(self, season: int) -> int:
        """Calculate offset for a season (negative sum of all previous seasons' episodes).

        Used when RSS episode numbers are absolute (e.g., S02E18 should be S02E05).
        Returns the offset to subtract from the parsed episode number.
        """
        if not self.season_episode_counts or season <= 1:
            return 0
        return -sum(self.season_episode_counts.get(s, 0) for s in range(1, season))


LANGUAGE = {"zh": "zh-CN", "jp": "ja-JP", "en": "en-US"}


def search_url(e, key="zh"):
    query = urlencode(
        {
            "api_key": _api_key(),
            "page": 1,
            "query": e,
            "include_adult": "false",
            "language": LANGUAGE[key],
        }
    )
    return f"{_tmdb_url()}/3/search/tv?{query}"


def search_movie_url(e, key="zh"):
    query = urlencode(
        {
            "api_key": _api_key(),
            "page": 1,
            "query": e,
            "include_adult": "false",
            "language": LANGUAGE[key],
        }
    )
    return f"{_tmdb_url()}/3/search/movie?{query}"


def info_url(e, key):
    return f"{_tmdb_url()}/3/tv/{e}?api_key={_api_key()}&language={LANGUAGE[key]}"


def season_url(tv_id, season_number, key):
    return f"{_tmdb_url()}/3/tv/{tv_id}/season/{season_number}?api_key={_api_key()}&language={LANGUAGE[key]}"


async def _fetch_tv_info(tv_id, language, req: RequestContent) -> dict | None:
    """拉一次剧集详情。失败时 get_json 返回 None（不抛异常）。

    详情里同时含 genres（判断是否动画）和 seasons（选季要用），拉一次复用，
    避免既有代码「is_animation 拉了详情只看 genres 就丢掉、命中后再拉一次
    同一个 URL」的重复请求。
    """
    return await req.get_json(info_url(tv_id, language))


def _is_animation_info(info: dict | None) -> bool:
    if not info:
        return False
    return any(genre.get("id") == 16 for genre in info.get("genres", []))


async def is_animation(tv_id, language, req: RequestContent) -> bool:
    return _is_animation_info(await _fetch_tv_info(tv_id, language, req))


async def get_season_episode_air_dates(
    tv_id: int, season_number: int, language: str, req: RequestContent
) -> list[dict]:
    """Get episode air dates for a season.

    Returns:
        List of {episode_number, air_date} dicts, sorted by episode number
    """
    import datetime

    url = season_url(tv_id, season_number, language)
    season_data = await req.get_json(url)
    if not season_data:
        return []

    episodes = []
    for ep in season_data.get("episodes", []):
        ep_num = ep.get("episode_number")
        air_date_str = ep.get("air_date")
        if ep_num and air_date_str:
            try:
                air_date = datetime.date.fromisoformat(air_date_str)
                episodes.append({"episode_number": ep_num, "air_date": air_date})
            except ValueError:
                continue

    return sorted(episodes, key=lambda x: x["episode_number"])


def detect_virtual_seasons(episodes: list[dict], gap_months: int = 6) -> list[int]:
    """Detect virtual season breakpoints based on air date gaps.

    When there's a gap > gap_months between consecutive episodes,
    it indicates a "cour break" or "virtual season" boundary.

    Args:
        episodes: List of {episode_number, air_date} dicts
        gap_months: Minimum gap in months to consider a season break (default 6)

    Returns:
        List of episode numbers where virtual seasons START (e.g., [1, 29] means S1 starts at ep1, S2 at ep29)
    """
    import datetime

    if len(episodes) < 2:
        return [1] if episodes else []

    virtual_season_starts = [1]  # First virtual season always starts at episode 1
    gap_days = gap_months * 30  # Approximate months to days

    for i in range(1, len(episodes)):
        prev_ep = episodes[i - 1]
        curr_ep = episodes[i]
        days_diff = (curr_ep["air_date"] - prev_ep["air_date"]).days

        if days_diff > gap_days:
            virtual_season_starts.append(curr_ep["episode_number"])
            logger.debug(
                "Detected virtual season break: %s days gap " "between ep%s and ep%s",
                days_diff,
                prev_ep["episode_number"],
                curr_ep["episode_number"],
            )

    return virtual_season_starts


async def get_aired_episode_count(
    tv_id: int, season_number: int, language: str, req: RequestContent
) -> int:
    """Get the count of episodes that have actually aired for a season.

    Args:
        tv_id: TMDB TV show ID
        season_number: Season number
        language: Language code
        req: Request content instance

    Returns:
        Number of episodes that have aired (air_date <= today)
    """
    import datetime

    url = season_url(tv_id, season_number, language)
    season_data = await req.get_json(url)
    if not season_data:
        return 0

    episodes = season_data.get("episodes", [])
    today = datetime.date.today()
    aired_count = 0

    for ep in episodes:
        air_date_str = ep.get("air_date")
        if air_date_str:
            try:
                air_date = datetime.date.fromisoformat(air_date_str)
                if air_date <= today:
                    aired_count += 1
            except ValueError:
                # Invalid date format, skip this episode
                continue

    logger.debug(
        "Season %s: %s aired of %s total episodes",
        season_number,
        aired_count,
        len(episodes),
    )
    return aired_count


def get_season(seasons: list) -> tuple[int, str | None]:
    ss = [s for s in seasons if s["air_date"] is not None and "特别" not in s["season"]]
    if not ss:
        return 1, None
    ss = sorted(ss, key=lambda e: e.get("air_date"), reverse=True)
    for season in ss:
        if re.search(r"第 \d+ 季", season.get("season")) is not None:
            date = season.get("air_date").split("-")
            [year, _, _] = date
            now_year = time.localtime().tm_year
            if int(year) <= now_year:
                return int(re.findall(r"\d+", season.get("season"))[0]), season.get(
                    "poster_path"
                )
    return len(ss), ss[-1].get("poster_path")


# TMDB 上 specials 在第 0 季，但 OVA / 剧场版合集常常挂在非 0 季号上，只能
# 再按名字排除。这是 get_season() 第 251 行「特别」过滤的超集，保证本模块
# 新旧两套季选择器的口径不会打架。
_SPECIAL_SEASON_NAME = re.compile(r"特别|特別|specials?|ova|oad", re.I)


def _parse_tmdb_date(value: str | None) -> datetime.date | None:
    """TMDB 的日期字段可能是 null，也可能是空串，两种都要挡住。"""
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


def _is_regular_season(season: dict) -> bool:
    """正片季判定。

    ``season_number`` 缺失时 ``.get(...) == 0`` 为 False，会把 specials 当
    正片，所以用 ``or 0`` 兜底再比较。
    """
    if (season.get("season_number") or 0) <= 0:
        return False
    return _SPECIAL_SEASON_NAME.search(season.get("name") or "") is None


def _closest_season(
    info: dict, target: datetime.date
) -> tuple[int, int, str | None] | None:
    """返回该候选里离 target 最近的正片季 ``(相差天数, 季号, 海报路径)``。

    没有任何可比日期时返回 None——这既不是「超出窗口」也不是「不是动画」，
    调用方必须把它当作「无法判断」而不是「不匹配」，否则最需要日期匹配的
    新番（TMDB 尚未录入放送日期）反而最容易被判为未匹配。
    """
    best: tuple[int, int, str | None] | None = None
    for season in info.get("seasons") or []:
        if not _is_regular_season(season):
            continue
        air = _parse_tmdb_date(season.get("air_date"))
        if air is None:
            continue
        candidate = (
            abs((air - target).days),
            int(season["season_number"]),
            season.get("poster_path"),
        )
        # 相差天数相同时取较小季号，保证结果可复现
        if best is None or candidate[:2] < best[:2]:
            best = candidate
    return best


def _season_from_episode_dates(
    season_nums: list[tuple[int, int]],
    episode_results: list,
    target: datetime.date,
) -> int | None:
    """用集级 air_date 定季：找离 target 最近的一集，返回它所属的季号。

    这才是分割放送的正解——TMDB 把「一季两 cour、中间隔半年」建模成一个
    season，季级 air_date 只记第一 cour 的日期，只有集级日期才落在正确的
    cour 上。数据来自调用方本来就要拉的 get_season_episode_air_dates()，
    不产生额外请求。
    """
    best: tuple[int, int] | None = None
    for (season_num, _), episodes in zip(season_nums, episode_results):
        if isinstance(episodes, BaseException) or not episodes:
            continue
        for episode in episodes:
            candidate = (abs((episode["air_date"] - target).days), season_num)
            if best is None or candidate < best:
                best = candidate
    return best[1] if best is not None else None


# 用集级日期推星期时的取样窗口：只看最近这么多集。长番跨 cour 会换档期，
# 最近的档期才是日历要展示的那个；窗口也天然排除了「第 1 集提前特别放送」
# 这类首播偏移。
_WEEKDAY_SAMPLE = 6


def _derive_air_weekday(
    info_content: dict,
    season_nums: list[tuple[int, int]],
    episode_results: list,
) -> int | None:
    """推导放送星期（0=周一 .. 6=周日），全部取自已经拉过的响应，零额外请求。

    优先级从「当周档期」到「史前首播」逐级降级：

    1. ``next_episode_to_air`` —— 下一集的播出日，正是日历要展示的那个
    2. ``last_episode_to_air`` —— 覆盖分割放送与刚完结的番
    3. 最高季的最近若干集 ``air_date`` 的众数 —— 吃掉挪档特别篇的噪声
    4. 最新一季的季级 ``air_date``
    5. ``first_air_date`` —— 只作最后兜底：它是「这部作品史上第一集」，
       多季/多 cour 番换档很常见，单独用它会写入过时的星期

    ``date.weekday()`` 天然就是 0=周一，无需像 bgm 的 1-7 那样换算。
    """
    for key in ("next_episode_to_air", "last_episode_to_air"):
        episode = info_content.get(key)
        if isinstance(episode, dict):
            aired = _parse_tmdb_date(episode.get("air_date"))
            if aired is not None:
                return aired.weekday()

    weekday = _weekday_from_episode_dates(season_nums, episode_results)
    if weekday is not None:
        return weekday

    seasons = [s for s in info_content.get("seasons") or [] if _is_regular_season(s)]
    latest = max(
        (s for s in seasons if _parse_tmdb_date(s.get("air_date")) is not None),
        key=lambda s: s.get("season_number") or 0,
        default=None,
    )
    if latest is not None:
        aired = _parse_tmdb_date(latest.get("air_date"))
        if aired is not None:
            return aired.weekday()

    first_air = _parse_tmdb_date(info_content.get("first_air_date"))
    return first_air.weekday() if first_air is not None else None


def _weekday_from_episode_dates(
    season_nums: list[tuple[int, int]], episode_results: list
) -> int | None:
    """取最高季最近 ``_WEEKDAY_SAMPLE`` 集播出日的众数星期。

    众数未过半时返回 None：档期本身就不规律，猜一个不如交给下一级来源。
    """
    latest: tuple[int, list] | None = None
    for (season_num, _), episodes in zip(season_nums, episode_results):
        if isinstance(episodes, BaseException) or not episodes:
            continue
        if latest is None or season_num > latest[0]:
            latest = (season_num, episodes)
    if latest is None:
        return None

    pool = [e["air_date"].weekday() for e in latest[1][-_WEEKDAY_SAMPLE:]]
    top = max(set(pool), key=pool.count)
    if len(pool) >= 3 and pool.count(top) * 2 <= len(pool):
        logger.debug("TMDB air weekday inconclusive for season %s: %s", latest[0], pool)
        return None
    return top


async def _select_by_air_date(
    contents: list,
    language,
    req: RequestContent,
    air_date: datetime.date,
) -> tuple[int | None, dict | None, int | None, str | None]:
    """阶段一：在搜索候选里按季级放送日期选出正确的那部番。

    返回 ``(matched_id, 详情 json, 季号, 海报路径)``。三种失败状态必须区分：

    - 一个动画候选都没有 → ``(None, None, None, None)``，调用方回退 search/movie
    - 有动画候选但全部超出窗口 → ``(None, 非 None, None, None)``，调用方据此
      判定「TMDB 上没有这部番」
    - 有动画候选但没有任何可比日期（新番未定档）→ 退回旧行为，返回首个动画
    """
    fallback_id: int | None = None
    fallback_info: dict | None = None
    best: tuple[int, int, int, str | None] | None = None
    best_id: int | None = None
    best_info: dict | None = None
    candidates = contents[:_AIR_DATE_MAX_CANDIDATES]
    if len(contents) > len(candidates):
        logger.debug(
            "Air-date matching only checks the first %s of %s TMDB candidates",
            len(candidates),
            len(contents),
        )
    for index, content in enumerate(candidates):
        cid = content["id"]
        info = await _fetch_tv_info(cid, language, req)
        if info is None or not _is_animation_info(info):
            continue
        if fallback_id is None:
            fallback_id, fallback_info = cid, info
        closest = _closest_season(info, air_date)
        if closest is None:
            continue
        delta, season_number, poster_path = closest
        # 相差天数相同时按搜索结果下标，保留 TMDB 的相关度顺序
        candidate = (delta, index, season_number, poster_path)
        if best is None or candidate[:3] < best[:3]:
            best, best_id, best_info = candidate, cid, info
    if fallback_id is None:
        return None, None, None, None
    if best is None:
        # TMDB 一个放送日期都没录（常见于刚定档的新番）：无从判断，退回旧行为
        logger.debug("No comparable TMDB air dates; falling back to first animation")
        return fallback_id, fallback_info, None, None
    if best[0] > _CANDIDATE_WINDOW_DAYS:
        logger.debug(
            "Closest TMDB season is %s days from %s; treating as no match",
            best[0],
            air_date,
        )
        return None, fallback_info, None, None
    return best_id, best_info, best[2], best[3]


async def _search_movie(
    title: str,
    language: str,
    req: RequestContent,
    air_date: datetime.date | None = None,
) -> TMDBInfo | None:
    """在 search/movie 端点查询电影/剧场版。

    电影没有季度概念，因此不复用剧集的季度/集数聚合逻辑，仅返回标题、原名、
    年份与海报等基本信息。

    ``air_date`` 只用于在多个候选里**排序**，绝不用于过滤：Mikan 上剧场版的
    「放送开始」多半是资源发布日，而 TMDB 的 release_date 是院线上映日，两者
    常差几个月到一两年。硬过滤会把正确的电影滤掉，比原本的 results[0] 更差。
    """
    url = search_movie_url(title, language)
    contents = await req.get_json(url)
    results = (contents or {}).get("results") or []
    if not results:
        url = search_movie_url(title.replace(" ", ""), language)
        contents = await req.get_json(url)
        results = (contents or {}).get("results") or []
    if not results:
        return None
    # 去空格重试会重新赋值 results，所以日期排序必须放在重试之后
    movie = results[0]
    if air_date is not None:
        dated = [
            (abs((released - air_date).days), index, candidate)
            for index, candidate in enumerate(results)
            if (released := _parse_tmdb_date(candidate.get("release_date"))) is not None
        ]
        if dated:
            # 相差天数相同时按搜索结果下标，保留 TMDB 的相关度顺序
            movie = min(dated, key=lambda item: item[:2])[2]
    year_number = (movie.get("release_date") or "").split("-")[0]
    poster_path = movie.get("poster_path")
    return TMDBInfo(
        id=movie["id"],
        title=movie.get("title") or title,
        original_title=movie.get("original_title") or title,
        season=[],
        last_season=0,
        year=str(year_number),
        poster_link=(
            f"https://image.tmdb.org/t/p/w780{poster_path}" if poster_path else None
        ),
        series_status=None,
        season_episode_counts=None,
        virtual_season_starts=None,
    )


async def tmdb_parser(
    title,
    language,
    test: bool = False,
    is_movie: bool = False,
    air_date: datetime.date | None = None,
) -> TMDBInfo | None:
    """按标题查询 TMDB。

    ``air_date`` 为番剧的放送开始日期（由 mix 解析器从 Mikan 主页取得）。
    传入时会用它在多个动画候选里挑出正确的那部并定位到具体季度，否则维持
    「取第一个动画」的既有行为。
    """
    # `test` must be part of the key: test mode returns the raw remote poster
    # URL instead of a locally-saved one, so mixing the two would poison
    # whichever caller queries second.
    # air_date 同理：带日期与不带日期的查询结果不同，共用键会互相污染。
    # Mikan 的放送开始是番剧级的稳定值（一个主页一个），不是逐种子的日期，
    # 不会把 512 条的 LRU 撑爆。
    cache_key = f"{title}:{language}:{test}:{is_movie}"
    if air_date is not None:
        cache_key = f"{cache_key}:{air_date.isoformat()}"
    if cache_key in _tmdb_cache:
        return _tmdb_cache[cache_key]

    async with RequestContent() as req:
        if is_movie:
            # 已知是电影/剧场版，直接查询 search/movie，跳过剧集搜索
            result = await _search_movie(title, language, req, air_date)
            _tmdb_cache[cache_key] = result
            return result
        url = search_url(title, language)
        contents = await req.get_json(url)
        if not contents:
            return await _search_movie(title, language, req, air_date)
        contents = (contents or {}).get("results") or []
        if not contents:
            url = search_url(title.replace(" ", ""), language)
            contents_resp = await req.get_json(url)
            if not contents_resp:
                return await _search_movie(title, language, req, air_date)
            contents = (contents_resp or {}).get("results") or []
            if not contents:
                # search/tv 无结果：回退到 search/movie (剧场版等)
                return await _search_movie(title, language, req, air_date)
        # 判断动画
        if contents:
            matched_id = None
            info_content = None
            air_date_season: int | None = None
            air_date_poster: str | None = None
            if air_date is None:
                for content in contents:
                    cid = content["id"]
                    info = await _fetch_tv_info(cid, language, req)
                    if _is_animation_info(info):
                        matched_id = cid
                        info_content = info
                        break
            else:
                matched_id, info_content, air_date_season, air_date_poster = (
                    await _select_by_air_date(contents, language, req, air_date)
                )
                if matched_id is None and info_content is not None:
                    # 有动画候选但全部超出选番窗口：视为 TMDB 上没有这部番。
                    # 不再回退 search/movie —— 调用方（mix）会退回 Mikan 的
                    # 官方名与海报。同样不缓存这个否定结果。
                    return None
            if matched_id is None:
                # search/tv 有结果但都不是动画：回退到 search/movie (剧场版等)。
                # Don't cache the negative result permanently — a temporary
                # TMDB hiccup shouldn't poison this title for the process lifetime.
                return await _search_movie(title, language, req, air_date)
            if not info_content:
                # matched_id 只在详情已确认是动画时才被赋值，所以这里进不来；
                # 保留分支是给 mypy 收窄 dict | None。
                return None
            season = [
                {
                    "season": s.get("name"),
                    "air_date": s.get("air_date"),
                    "poster_path": s.get("poster_path"),
                    # 原本丢掉了季号，导致 get_season() 只能靠中文正则
                    # 「第 N 季」回推。get_season() 不读这个 key，加它是安全的。
                    "season_number": s.get("season_number"),
                }
                for s in info_content.get("seasons") or []
            ]
            last_season, poster_path = get_season(season)
            # Extract series status (e.g., "Ended", "Returning Series")
            series_status = info_content.get("status")
            # Extract episode counts per season (exclude specials at season 0)
            # For ongoing series, we need to get actual aired episode counts
            season_episode_counts = {}
            virtual_season_starts = {}
            season_nums = [
                (s.get("season_number", 0), s.get("episode_count", 0))
                for s in info_content.get("seasons", [])
                if s.get("season_number", 0) > 0
            ]
            episode_results = await asyncio.gather(
                *[
                    get_season_episode_air_dates(matched_id, sn, language, req)
                    for sn, _ in season_nums
                ],
                return_exceptions=True,
            )
            for (season_num, total_eps), episodes in zip(season_nums, episode_results):
                if isinstance(episodes, BaseException):
                    logger.warning(
                        "Failed to get episodes for season %s: %s",
                        season_num,
                        episodes,
                    )
                    season_episode_counts[season_num] = total_eps
                    continue
                if episodes:
                    # Detect virtual seasons based on air date gaps
                    vs_starts = detect_virtual_seasons(episodes)
                    if len(vs_starts) > 1:
                        virtual_season_starts[season_num] = vs_starts
                        logger.debug(
                            "Season %s has virtual seasons starting at episodes: %s",
                            season_num,
                            vs_starts,
                        )
                    # Count only aired episodes
                    season_episode_counts[season_num] = len(episodes)
                else:
                    season_episode_counts[season_num] = total_eps
            matched_season = air_date_season
            if air_date is not None:
                # 阶段二：用刚拉到的集级 air_date 精确定季。季级日期对分割放送
                # 是错的（TMDB 把两个 cour 合成一季，只记第一 cour 的日期），
                # 集级日期才落在正确的 cour 上。拿不到集级日期时沿用阶段一。
                matched_season = (
                    _season_from_episode_dates(season_nums, episode_results, air_date)
                    or air_date_season
                )
                if matched_season is not None and matched_season != air_date_season:
                    air_date_poster = next(
                        (
                            s.get("poster_path")
                            for s in season
                            if s.get("season_number") == matched_season
                        ),
                        air_date_poster,
                    )
                if air_date_poster:
                    poster_path = air_date_poster
            if poster_path is None:
                poster_path = info_content.get("poster_path")
            # 与 _search_movie 一致地回退到查询词：TMDBInfo 的这两个字段声明
            # 为 str，之前靠 req 是 Any 才没暴露出可能传 None
            original_title = info_content.get("original_name") or title
            official_title = info_content.get("name") or title
            year_number = (info_content.get("first_air_date") or "").split("-")[0]
            if poster_path:
                if not test:
                    poster_url = f"https://image.tmdb.org/t/p/w780{poster_path}"
                    img = await req.get_content(poster_url)
                    # img is None if the poster download failed; don't crash on it.
                    poster_link = (
                        await save_image(img, "jpg", source_url=poster_url)
                        if img
                        else None
                    )
                else:
                    poster_link = "https://image.tmdb.org/t/p/w780" + poster_path
            else:
                poster_link = None
            result = TMDBInfo(
                id=matched_id,
                title=official_title,
                original_title=original_title,
                season=season,
                last_season=last_season,
                year=str(year_number),
                poster_link=poster_link,
                series_status=series_status,
                season_episode_counts=season_episode_counts,
                virtual_season_starts=(
                    virtual_season_starts if virtual_season_starts else None
                ),
                matched_season=matched_season,
                air_weekday=_derive_air_weekday(
                    info_content, season_nums, episode_results
                ),
            )
            if len(_tmdb_cache) >= _TMDB_CACHE_MAX:
                _tmdb_cache.popitem(last=False)
            _tmdb_cache[cache_key] = result
            return result
        else:
            # No results at all — don't cache the negative result permanently,
            # see the matched_id is None case above.
            return None


if __name__ == "__main__":
    import asyncio

    print(asyncio.run(tmdb_parser("魔法禁书目录", "zh")))
