"""自定义文件名模板的渲染与校验。

模板语法沿用通知模块的 ``{{var}}`` 纯字符串替换（``notification/base.py`` 的
``_format_message``），不引入模板引擎：用户模板是不可信输入，让它具备表达式
求值能力没有必要的风险，而文件名场景也用不上循环/条件。

季度和集数额外支持 ``{{season:2}}`` 形式的补零宽度。不带宽度就是原值，所以
不需要另设一套 ``*_nopad`` 变量。

本模块刻意不 import 任何 model 或 settings，只吃基本类型——这样重命名管线
与设置页的预览接口可以共用同一份实现，不会漂移。
"""

from __future__ import annotations

import re
from collections.abc import Mapping

# 这三个是切到 template 模式时预填给用户的模板。它们**刻意不等价于 pn**：
# pn 用的是从文件名解析出的 parser_title，而这里用数据库里的 official_title
# 和字幕组，命名质量更高。因为 template 是 opt-in 模式，不会影响任何没主动
# 切过去的用户。
DEFAULT_EPISODE_TEMPLATE = "[{{group}}] {{official_title}} S{{season:2}}E{{episode:2}}"
DEFAULT_MOVIE_TEMPLATE = "[{{group}}] {{official_title}} ({{year}})"
# 等价于 path.py 原本的 f"{title} ({year})" if year else title——年份为空时
# 渲染出的空 "()" 会被 _cleanup 清掉，所以不需要两套模板。
DEFAULT_FOLDER_TEMPLATE = "{{official_title}} ({{year}})"

# (变量名, 中文说明)。说明只用于日志与错误提示；界面文案走 i18n。
TEMPLATE_VARIABLES: tuple[tuple[str, str], ...] = (
    ("parser_title", "标题解析器从种子文件名里得到的标题"),
    ("official_title", "番剧的官方名（数据库里的 official_title）"),
    ("folder_name", "番剧文件夹名，通常已带年份"),
    ("season", "季度，可用 {{season:2}} 补零"),
    ("episode", "集数，可用 {{episode:2}} 补零；总集篇等半集保留小数"),
    ("group", "字幕组"),
    ("year", "年份"),
    ("resolution", "分辨率"),
    ("source", "片源"),
    ("subtitle", "字幕语言"),
    ("language", "字幕文件的语言代码，仅字幕文件可用"),
)

VARIABLE_NAMES = frozenset(name for name, _ in TEMPLATE_VARIABLES)

# 文件夹在**添加种子时**生成，那一刻只有 Bangumi 数据库行，没有解析过的
# 文件——所以 parser_title / episode 拿不到，folder_name 也不能自我引用。
# season 也刻意不给：gen_save_path 已经单独建了 "Season N" 子目录，季度写进
# 番剧文件夹名只会重复一遍。
FOLDER_TEMPLATE_VARIABLES: tuple[tuple[str, str], ...] = (
    ("official_title", "番剧的官方名"),
    ("year", "年份"),
    ("group", "字幕组"),
    ("resolution", "分辨率"),
    ("source", "片源"),
    ("subtitle", "字幕语言"),
)

FOLDER_VARIABLE_NAMES = frozenset(name for name, _ in FOLDER_TEMPLATE_VARIABLES)

# 只有这两个是数字，也只有它们接受 :N 补零宽度
NUMERIC_VARIABLES = frozenset({"season", "episode"})

# 集数缺失时整包文件会渲染成同一个名字、逐个改名互相覆盖，见 validate_template
_EPISODE_VARIABLE = "episode"

_MAX_PAD_WIDTH = 4

_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)(?::(\d+))?\s*\}\}")

# 路径分隔符会把文件重命名进子目录，或直接让下载器报错。这是模板路径独有的
# 清洗——renamer.gen_path 对既有方法明确不做保留字符清洗（#721：清洗会让老
# 做种库在升级后被整库重命名），那条约束不受这里影响。
_PATH_SEPARATORS = re.compile(r"[/\\]")

# 变量渲染为空后留下的空壳，如 group 为空时的 "[]"
_EMPTY_BRACKETS = re.compile(r"[\[\(【（]\s*[\]\)】）]")
_MULTI_SPACE = re.compile(r"\s{2,}")
_DANGLING_TAIL = re.compile(r"\s+[-_]+$")
_DANGLING_HEAD = re.compile(r"^[-_]+\s+")


def format_number(value: int | float, width: int = 0) -> str:
    """按补零宽度渲染季度/集数。

    总集篇等半集（12.5）保留小数，否则会覆盖同季的整数集 (#667)；补零只作用
    于整数部分，所以 ``{{episode:2}}`` 对 5.5 得到 ``05.5``、对 12.5 得到
    ``12.5``。``width=0``（模板里不写 ``:N``）就是原值。
    """
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, float):
        whole, _, frac = str(value).partition(".")
        head = f"{int(whole):0{width}d}" if width else whole
        return f"{head}.{frac}"
    return f"{value:0{width}d}" if width else str(value)


def validate_template(
    template: str,
    *,
    allowed: frozenset[str] = VARIABLE_NAMES,
    require_episode: bool = False,
) -> str | None:
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
    used: set[str] = set()
    for match in _PLACEHOLDER.finditer(template):
        name, width = match.group(1), match.group(2)
        used.add(name)
        if name not in allowed:
            return f"未知变量：{{{{{name}}}}}"
        if width is None:
            continue
        if name not in NUMERIC_VARIABLES:
            return f"{{{{{name}}}}} 不是数字，不能写补零宽度"
        if not 1 <= int(width) <= _MAX_PAD_WIDTH:
            return f"补零宽度必须在 1 到 {_MAX_PAD_WIDTH} 之间：{match.group(0)}"
    if require_episode and _EPISODE_VARIABLE not in used:
        return (
            "剧集模板必须包含 {{episode}}（可写 {{episode:2}} 补零），"
            "否则同一合集内的剧集会重名并互相覆盖"
        )
    return None


def build_fields(
    *,
    parser_title: str,
    official_title: str | None = None,
    folder_name: str = "",
    season: int = 1,
    episode: int | float = 1,
    group: str | None = None,
    year: str | None = None,
    resolution: str | None = None,
    source: str | None = None,
    subtitle: str | None = None,
    language: str | None = None,
) -> dict[str, object]:
    """把重命名管线里散落的字段整理成模板变量表。

    重命名管线与预览接口都走这里，保证两边的变量语义完全一致。季度和集数保留
    数字类型——补零宽度在替换时才知道。
    """
    return {
        "parser_title": parser_title or "",
        "official_title": official_title or "",
        "folder_name": folder_name or "",
        "season": season,
        "episode": episode,
        "group": group or "",
        "year": year or "",
        "resolution": resolution or "",
        "source": source or "",
        "subtitle": subtitle or "",
        "language": language or "",
    }


def build_folder_fields(**kwargs: object) -> dict[str, object]:
    """文件夹模板专用的变量表：只保留 FOLDER_VARIABLE_NAMES 里的键。

    这样"渲染"和"校验"的口径完全一致——手改 config.json 塞进 {{episode}}
    也只会渲染成空，而不是悄悄用上一个本不该有的值。
    """
    fields = build_fields(**kwargs)  # type: ignore[arg-type]
    return {k: v for k, v in fields.items() if k in FOLDER_VARIABLE_NAMES}


def render_template(
    template: str,
    fields: Mapping[str, object],
    suffix: str = "",
) -> str:
    """按模板渲染文件名。

    取不到值的变量渲染成空串，随后清掉因此留下的空 ``[]`` / ``()`` 与多余空格
    ——否则 ``{{parser_title}} [{{group}}]`` 在没有字幕组时会产出 ``Title []``。

    ``suffix`` 是含点的扩展名，由代码无条件追加：交给用户写容易漏，漏了文件就
    没有扩展名，媒体库直接不认。
    """

    def _substitute(match: re.Match[str]) -> str:
        name, width = match.group(1), match.group(2)
        value = fields.get(name)
        if value is None or value == "":
            return ""
        if name in NUMERIC_VARIABLES and isinstance(value, (int, float)):
            return format_number(value, int(width) if width else 0)
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
    # 清掉变量落空后悬在首尾的连接符（"{{parser_title}} - {{group}}" → "Title -"）。
    # 必须要求连接符与空格相邻：直接 strip("-_") 会把 "_Underscore_" 这类
    # 本来就以下划线收尾的标题咬掉一截。
    text = _DANGLING_TAIL.sub("", text)
    text = _DANGLING_HEAD.sub("", text)
    return text.strip()
