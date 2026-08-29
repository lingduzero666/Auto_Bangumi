"""自定义文件名模板的渲染与校验。

模板语法沿用通知模块的 ``{{var}}`` 纯字符串替换（``notification/base.py`` 的
``_format_message``），不引入模板引擎：用户模板是不可信输入，让它具备表达式
求值能力没有必要的风险，而文件名场景也用不上循环/条件。

本模块刻意不 import 任何 model 或 settings，只吃基本类型——这样重命名管线
与设置页的预览接口可以共用同一份实现，不会漂移。
"""

from __future__ import annotations

import re
from collections.abc import Mapping

# 默认模板必须逐字节复现现有 pn 方法的输出，见 test_rename_template.py 的
# 等价性回归测试。改这两个常量等于改所有未自定义模板用户的文件名。
DEFAULT_EPISODE_TEMPLATE = "{{title}} S{{season}}E{{episode}}"
DEFAULT_MOVIE_TEMPLATE = "{{title}}"

# (变量名, 中文说明)。说明只用于日志与错误提示；界面文案走 i18n。
TEMPLATE_VARIABLES: tuple[tuple[str, str], ...] = (
    ("title", "从文件名解析出的标题"),
    ("bangumi_name", "番剧文件夹名，通常带年份"),
    ("season", "季度，补零到两位"),
    ("episode", "集数，补零到两位；总集篇等半集保留小数"),
    ("season_nopad", "季度，不补零"),
    ("episode_nopad", "集数，不补零"),
    ("group", "字幕组"),
    ("year", "年份"),
    ("resolution", "分辨率"),
    ("source", "片源"),
    ("subtitle", "字幕语言"),
    ("language", "字幕文件的语言代码，仅字幕文件可用"),
)

VARIABLE_NAMES = frozenset(name for name, _ in TEMPLATE_VARIABLES)

_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")

# 路径分隔符会把文件重命名进子目录，或直接让下载器报错。这是模板路径独有的
# 清洗——renamer.gen_path 对既有方法明确不做保留字符清洗（#721：清洗会让老
# 做种库在升级后被整库重命名），那条约束不受这里影响。
_PATH_SEPARATORS = re.compile(r"[/\\]")

# 变量渲染为空后留下的空壳，如 group 为空时的 "[]"
_EMPTY_BRACKETS = re.compile(r"[\[\(【（]\s*[\]\)】）]")
_MULTI_SPACE = re.compile(r"\s{2,}")
_DANGLING_TAIL = re.compile(r"\s+[-_]+$")
_DANGLING_HEAD = re.compile(r"^[-_]+\s+")


def format_episode(episode: int | float) -> str:
    """集数的显示形式。

    总集篇等半集（12.5）保留小数，否则会覆盖同季的整数集 (#667)；整数值补两位零。
    """
    if isinstance(episode, float) and episode.is_integer():
        episode = int(episode)
    return f"0{episode}" if episode < 10 else str(episode)


def format_season(season: int) -> str:
    """季度的显示形式，补两位零。"""
    return f"0{season}" if season < 10 else str(season)


def _unpadded(number: int | float) -> str:
    """不补零的显示形式。12.0 归一成 12，12.5 保留小数。"""
    if isinstance(number, float) and number.is_integer():
        number = int(number)
    return str(number)


_EPISODE_VARIABLES = frozenset({"episode", "episode_nopad"})


def validate_template(template: str, *, require_episode: bool = False) -> str | None:
    """校验模板，返回错误信息；合法返回 ``None``。

    空模板视为合法——重命名侧会把它当作「什么都不做」，界面上也不该因为用户
    还没输入就飘红。

    ``require_episode`` 用于剧集模板：季度合集里的每个文件都用同一个模板渲染，
    模板里没有集数变量的话整包文件会渲染成**同一个名字**，逐个重命名过去就是
    互相覆盖。这必须在保存时拒绝，不能只警告。
    """
    if not template.strip():
        return None
    if _PATH_SEPARATORS.search(template):
        return "模板不能包含路径分隔符 / 或 \\"
    used = {match.group(1) for match in _PLACEHOLDER.finditer(template)}
    unknown = sorted(used - VARIABLE_NAMES)
    if unknown:
        return "未知变量：" + "、".join(f"{{{{{name}}}}}" for name in unknown)
    if require_episode and not (used & _EPISODE_VARIABLES):
        return "剧集模板必须包含 {{episode}} 或 {{episode_nopad}}，否则同一合集内的剧集会重名并互相覆盖"
    return None


def build_fields(
    *,
    title: str,
    bangumi_name: str,
    season: int,
    episode: int | float,
    group: str | None = None,
    year: str | None = None,
    resolution: str | None = None,
    source: str | None = None,
    subtitle: str | None = None,
    language: str | None = None,
) -> dict[str, str]:
    """把重命名管线里散落的字段整理成模板变量表。

    重命名管线与预览接口都走这里，保证两边的变量语义完全一致。
    """
    return {
        "title": title or "",
        "bangumi_name": bangumi_name or "",
        "season": format_season(season),
        "episode": format_episode(episode),
        "season_nopad": _unpadded(season),
        "episode_nopad": _unpadded(episode),
        "group": group or "",
        "year": year or "",
        "resolution": resolution or "",
        "source": source or "",
        "subtitle": subtitle or "",
        "language": language or "",
    }


def render_template(
    template: str,
    fields: Mapping[str, str],
    suffix: str = "",
) -> str:
    """按模板渲染文件名。

    取不到值的变量渲染成空串，随后清掉因此留下的空 ``[]`` / ``()`` 与多余空格
    ——否则 ``{{title}} [{{group}}]`` 在没有字幕组时会产出 ``Title []``。

    ``suffix`` 是含点的扩展名。模板里没写 ``{{suffix}}`` 时自动补在末尾，避免
    用户漏写导致文件失去扩展名（那会让媒体库直接不认）。
    """

    def _substitute(match: re.Match[str]) -> str:
        value = fields.get(match.group(1))
        if not value:
            return ""
        # 变量值本身也可能含分隔符（如标题里带 "/"）——保存时的模板校验拦不住它
        return _PATH_SEPARATORS.sub(" ", str(value))

    rendered = _cleanup(_PLACEHOLDER.sub(_substitute, template))
    if suffix and not rendered.endswith(suffix):
        rendered += suffix
    return rendered


def _cleanup(text: str) -> str:
    previous = ""
    # 嵌套空壳（如 "[()]"）一轮清不干净，清到不动为止
    while previous != text:
        previous = text
        text = _EMPTY_BRACKETS.sub("", text)
    text = _MULTI_SPACE.sub(" ", text).strip()
    # 清掉变量落空后悬在首尾的连接符（"{{title}} - {{group}}" → "Title -"）。
    # 必须要求连接符与空格相邻：直接 strip("-_") 会把 "_Underscore_" 这类
    # 本来就以下划线收尾的标题咬掉一截。
    text = _DANGLING_TAIL.sub("", text)
    text = _DANGLING_HEAD.sub("", text)
    return text.strip()
