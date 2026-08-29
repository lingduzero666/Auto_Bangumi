"""Tests for the Mikan parser (cache + HTML extraction).

页面结构按真实站点构造，**不要**把放送信息塞进剧集页 fixture：真实的
/Home/Episode/{hash} 只有「发布日期」和「文件大小」，放送开始与放送日期都在
它链过去的 /Home/Bangumi/{id} 上。之前的 fixture 假设剧集页含「放送开始」，
导致 air_date 在生产中恒为 None 而测试全绿。
"""

import datetime
import importlib
from unittest.mock import AsyncMock

import pytest

# `module.parser.analyser.__init__` re-exports the `mikan_parser` function under
# the same name as this submodule, shadowing the submodule on the package
# object — so `import module.parser.analyser.mikan_parser as x` would resolve
# to the function, not the module. Go through importlib to get the module.
mikan_parser_module = importlib.import_module("module.parser.analyser.mikan_parser")
MikanInfo = mikan_parser_module.MikanInfo
mikan_parser = mikan_parser_module.mikan_parser

_HOST = "https://mikanani.me"
_HOMEPAGE = f"{_HOST}/Home/Episode/c89b3c6f0c1c0567a618f5288b853823"
_BANGUMI_PAGE = f"{_HOST}/Home/Bangumi/3600"


_EPISODE_PAGE = """
<html><body>
  <div class="bangumi-poster"
       style="background-image: url('/images/Bangumi/202604/abc.jpg?width=400');">
  </div>
  <p class="bangumi-title">
    <a href="/Home/Bangumi/3600#583">葬送的芙莉莲 第二季</a>
  </p>
  <p class="bangumi-info">发布日期：2026/04/03 23:47</p>
  <p class="bangumi-info">文件大小：8.3GB</p>
</body></html>
"""


def _bangumi_page(
    weekday_line: str = "放送日期：星期五",
    air_date_line: str = "放送开始：4/3/2026",
) -> str:
    return f"""
    <html><body>
      <p class="bangumi-info">官方网站：<a href="https://x.jp">x.jp</a></p>
      <p class="bangumi-info">{weekday_line}</p>
      <p class="bangumi-info">{air_date_line}</p>
      <p class="bangumi-info">Bangumi番组计划链接：
        <a href="https://bgm.tv/subject/1">https://bgm.tv/subject/1</a></p>
    </body></html>
    """


@pytest.fixture(autouse=True)
def _clear_cache():
    """conftest 没有清理这些模块级缓存的 autouse fixture，测试要自己来。"""
    mikan_parser_module.reset_cache()
    yield
    mikan_parser_module.reset_cache()


@pytest.fixture
def mikan_pages(mocker):
    """Patch the network + image-saving seams; return a per-URL page setter.

    get_html 按 URL 分派，模拟真实的「剧集页 -> 番剧主页」两跳，并记录每个
    URL 被抓了几次，供缓存用例断言。
    """

    def _install(
        episode: str | None = _EPISODE_PAGE,
        bangumi: str | None = None,
        extra: dict[str, str | None] | None = None,
    ):
        pages: dict[str, str | None] = {
            _HOMEPAGE: episode,
            _BANGUMI_PAGE: _bangumi_page() if bangumi is None else bangumi,
        }
        pages.update(extra or {})
        calls: dict[str, int] = {}

        async def fake_get_html(url: str) -> str | None:
            calls[url] = calls.get(url, 0) + 1
            return pages.get(url)

        async def fake_get_content(url: str) -> bytes:
            return b"fake-image-bytes"

        mocker.patch.object(
            mikan_parser_module.RequestContent, "get_html", side_effect=fake_get_html
        )
        mocker.patch.object(
            mikan_parser_module.RequestContent,
            "get_content",
            side_effect=fake_get_content,
        )
        mocker.patch.object(
            mikan_parser_module,
            "save_image",
            new=AsyncMock(return_value="posters/abc.jpg"),
        )
        return calls

    return _install


def test_reset_cache_clears_both_caches():
    """reset_cache() must drop every cached lookup (called on config reload so
    a changed endpoint stops serving stale results)."""
    stale = "https://mikanani.me/Home/Episode/stale"
    mikan_parser_module._mikan_cache[stale] = MikanInfo("", "Stale Title")
    mikan_parser_module._bangumi_page_cache[_BANGUMI_PAGE] = (None, 3)

    mikan_parser_module.reset_cache()

    assert len(mikan_parser_module._mikan_cache) == 0
    assert len(mikan_parser_module._bangumi_page_cache) == 0


async def test_parses_title_poster_and_broadcast_info(mikan_pages):
    """放送信息来自番剧主页，不是剧集页。

    这条用例是本模块的核心回归防线：把放送信息塞回剧集页 fixture 就会重现
    「air_date 恒为 None 但测试全绿」的问题。
    """
    mikan_pages()

    info = await mikan_parser(_HOMEPAGE)

    assert info.official_title == "葬送的芙莉莲"  # 「第.*季」被剥掉
    assert info.poster_link == "posters/abc.jpg"
    assert info.air_date == datetime.date(2026, 4, 3)
    assert info.air_weekday == 4  # 星期五


async def test_episode_page_alone_yields_no_broadcast_info(mikan_pages):
    """真实剧集页不含放送信息：主页抓不到时两个字段都为空，但标题/海报照常。"""
    mikan_pages(bangumi="")

    info = await mikan_parser(_HOMEPAGE)

    assert info.air_date is None
    assert info.air_weekday is None
    assert info.official_title == "葬送的芙莉莲"
    assert info.poster_link == "posters/abc.jpg"


async def test_subgroup_anchor_is_stripped_from_bangumi_url(mikan_pages):
    """href 上的 `#583` 是字幕组锚点，必须去掉，否则主页缓存键会按字幕组分裂。"""
    calls = mikan_pages()

    await mikan_parser(_HOMEPAGE)

    assert calls.get(_BANGUMI_PAGE) == 1
    assert f"{_BANGUMI_PAGE}#583" not in calls


async def test_bangumi_url_uses_the_host_of_the_episode_url(mikan_pages):
    """镜像域（mikanime.tv 等）也要能用：host 取自传入的剧集页 URL。"""
    mirror_episode = "https://mikanime.tv/Home/Episode/c89b3c6f0c1c0567a618"
    mirror_bangumi = "https://mikanime.tv/Home/Bangumi/3600"
    calls = mikan_pages(
        extra={mirror_episode: _EPISODE_PAGE, mirror_bangumi: _bangumi_page()}
    )

    info = await mikan_parser(mirror_episode)

    assert calls.get(mirror_bangumi) == 1
    assert info.air_weekday == 4


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("放送日期：星期一", 0),
        ("放送日期：星期二", 1),
        ("放送日期：星期三", 2),
        ("放送日期：星期四", 3),
        ("放送日期：星期五", 4),
        ("放送日期：星期六", 5),
        ("放送日期：星期日", 6),
        ("放送日期：星期天", 6),
        ("放送日期：周日", 6),
        ("放送日期：週三", 2),
        ("放送日期: 星期二", 1),  # 半角冒号
    ],
)
async def test_air_weekday_variants(mikan_pages, line, expected):
    mikan_pages(bangumi=_bangumi_page(weekday_line=line))

    info = await mikan_parser(_HOMEPAGE)

    assert info.air_weekday == expected


async def test_unknown_weekday_wording_is_ignored(mikan_pages):
    """「不定期」这类无法映射的字样返回 None，不抛异常。"""
    mikan_pages(bangumi=_bangumi_page(weekday_line="放送日期：不定期"))

    info = await mikan_parser(_HOMEPAGE)

    assert info.air_weekday is None
    assert info.air_date == datetime.date(2026, 4, 3)


async def test_air_date_accepts_fullwidth_and_halfwidth_colon(mikan_pages):
    mikan_pages(bangumi=_bangumi_page(air_date_line="放送开始: 12/25/2025"))

    info = await mikan_parser(_HOMEPAGE)

    assert info.air_date == datetime.date(2025, 12, 25)


async def test_missing_air_date_line_is_not_an_error(mikan_pages):
    """日期是可选增强：缺了只把 air_date 置空，星期/标题/海报照常返回。"""
    mikan_pages(bangumi=_bangumi_page(air_date_line="首播时间未定"))

    info = await mikan_parser(_HOMEPAGE)

    assert info.air_date is None
    assert info.air_weekday == 4
    assert info.official_title == "葬送的芙莉莲"
    assert info.poster_link == "posters/abc.jpg"


async def test_invalid_air_date_is_ignored(mikan_pages):
    """13 月 45 日这类非法日期必须返回 None，绝不能抛异常——
    调用方的 except AttributeError 语义是「页面结构变了」。"""
    mikan_pages(bangumi=_bangumi_page(air_date_line="放送开始：13/45/2026"))

    info = await mikan_parser(_HOMEPAGE)

    assert info.air_date is None
    assert info.official_title == "葬送的芙莉莲"


async def test_unreachable_bangumi_page_is_not_an_error(mikan_pages):
    """主页 404 / 网络失败只丢掉放送信息，不影响剧集页拿到的标题与海报。"""
    mikan_pages(extra={_BANGUMI_PAGE: None})  # get_html 对主页返回 None

    info = await mikan_parser(_HOMEPAGE)

    assert info.air_date is None
    assert info.air_weekday is None
    assert info.official_title == "葬送的芙莉莲"


async def test_broken_episode_page_still_raises_attribute_error(mikan_pages):
    """海报/标题元素缺失时仍必须抛 AttributeError（rss/analyser.py 依赖它
    区分「抓取或解析失败」与「正常无数据」）。"""
    mikan_pages(episode="<html><body><p>放送开始：4/3/2026</p></body></html>")

    with pytest.raises(AttributeError):
        await mikan_parser(_HOMEPAGE)


async def test_result_is_cached_by_episode_homepage(mikan_pages):
    calls = mikan_pages()

    first = await mikan_parser(_HOMEPAGE)
    second = await mikan_parser(_HOMEPAGE)

    assert first is second
    assert calls[_HOMEPAGE] == 1
    assert mikan_parser_module._mikan_cache[_HOMEPAGE].air_date == datetime.date(
        2026, 4, 3
    )


async def test_bangumi_page_is_fetched_once_across_episode_pages(mikan_pages):
    """同一部番的不同剧集页（不同字幕组/集数）共享一次番剧主页请求。"""
    other_episode = f"{_HOST}/Home/Episode/0000000000000000000000000000000000000000"
    calls = mikan_pages(extra={other_episode: _EPISODE_PAGE})

    first = await mikan_parser(_HOMEPAGE)
    second = await mikan_parser(other_episode)

    assert calls[_BANGUMI_PAGE] == 1
    assert first.air_weekday == second.air_weekday == 4


async def test_empty_bangumi_page_result_is_not_cached(mikan_pages):
    """主页没有任何放送信息时不进缓存：Mikan 后来补上就该能取到。"""
    mikan_pages(bangumi="<html><body></body></html>")

    await mikan_parser(_HOMEPAGE)

    assert _BANGUMI_PAGE not in mikan_parser_module._bangumi_page_cache
