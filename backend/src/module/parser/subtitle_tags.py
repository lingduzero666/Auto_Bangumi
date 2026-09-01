"""字幕语言与压制方式的规范化。

字幕组对同一种字幕的写法差异极大——「简体内嵌」「[CHS][内嵌]」「GB_MP4」
指的都是同一件事。按原始字符串做偏好匹配必然漏判，所以这里把它们归一到
一组有限的枚举值上，让 ``preferred_subtitle_language`` /
``preferred_subtitle_style`` 能可靠命中。

只识别明确的语言/压制标记，认不出的维度返回 ``None``：宁可不参与去重，
也不能靠猜把用户想要的版本跳过。同理，这里不从容器格式反推压制方式
（MP4 常内嵌、MKV 常内封只是习惯，并非规则）。
"""

from __future__ import annotations

import re

from module.parser.analyser.tokenizer.result import ParsedRelease

# 存库与前端下拉共用的规范值。取值刻意用英文短码而非中文，避免简繁写法
# 本身又变成一个需要规范化的维度。
SUBTITLE_LANGUAGE_CHS = "chs"
SUBTITLE_LANGUAGE_CHT = "cht"
SUBTITLE_LANGUAGE_CHS_CHT = "chs_cht"
SUBTITLE_LANGUAGE_JPN = "jpn"
SUBTITLE_LANGUAGE_ENG = "eng"

SUBTITLE_STYLE_EMBEDDED = "embedded"
SUBTITLE_STYLE_MUXED = "muxed"
SUBTITLE_STYLE_EXTERNAL = "external"

SUBTITLE_LANGUAGES: tuple[str, ...] = (
    SUBTITLE_LANGUAGE_CHS,
    SUBTITLE_LANGUAGE_CHT,
    SUBTITLE_LANGUAGE_CHS_CHT,
    SUBTITLE_LANGUAGE_JPN,
    SUBTITLE_LANGUAGE_ENG,
)

SUBTITLE_STYLES: tuple[str, ...] = (
    SUBTITLE_STYLE_EMBEDDED,
    SUBTITLE_STYLE_MUXED,
    SUBTITLE_STYLE_EXTERNAL,
)

# 顺序即优先级：双语写法必须排在单语之前，否则「简繁内封」会被「简」抢先
# 命中成简体。同理「简日双语」这类中日对照仍按其中的中文归类，因为用户挑
# 的是中文字幕的简繁，日文只是伴随音轨/对照。
_LANGUAGE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        SUBTITLE_LANGUAGE_CHS_CHT,
        re.compile(r"简繁|簡繁|繁简|繁簡|CHS[\W_]?CHT|CHT[\W_]?CHS", re.I),
    ),
    (
        SUBTITLE_LANGUAGE_CHT,
        re.compile(
            r"繁体|繁體|繁中|繁日|正体|正體|"
            r"CHT(?![A-Za-z0-9])|BIG5(?![A-Za-z0-9])|JPTC(?![A-Za-z0-9])",
            re.I,
        ),
    ),
    (
        SUBTITLE_LANGUAGE_CHS,
        re.compile(
            r"简体|簡体|简中|簡中|简日|簡日|"
            r"CHS(?![A-Za-z0-9])|GB(?![A-Za-z0-9])|JPSC(?![A-Za-z0-9])",
            re.I,
        ),
    ),
    (
        SUBTITLE_LANGUAGE_JPN,
        re.compile(r"日语|日語|日文|JPN(?![A-Za-z0-9])", re.I),
    ),
    (
        SUBTITLE_LANGUAGE_ENG,
        re.compile(r"英语|英語|英文|ENG(?![A-Za-z0-9])|VOSTFR(?![A-Za-z0-9])", re.I),
    ),
)

# 「内嵌」与「内封」共用一个「内」字，但两者都是完整词，不会互相误命中。
_STYLE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (SUBTITLE_STYLE_EMBEDDED, re.compile(r"内嵌|內嵌|硬字幕|硬嵌", re.I)),
    (SUBTITLE_STYLE_MUXED, re.compile(r"内封|內封", re.I)),
    (SUBTITLE_STYLE_EXTERNAL, re.compile(r"外挂|外掛|外置", re.I)),
)


def parse_subtitle_tags(text: str | None) -> tuple[str | None, str | None]:
    """从任意文本里提取 ``(语言, 压制)`` 规范值，认不出的维度给 ``None``。"""
    if not text:
        return None, None
    language = next(
        (value for value, pattern in _LANGUAGE_PATTERNS if pattern.search(text)),
        None,
    )
    style = next(
        (value for value, pattern in _STYLE_PATTERNS if pattern.search(text)),
        None,
    )
    return language, style


def release_subtitle_tags(release: ParsedRelease) -> tuple[str | None, str | None]:
    """取一条发布的字幕标签，缺哪一维就回退去扫完整标题。

    解析器对「[CHS][内嵌]」这种拆在多个方括号里的标签只会留下其中一个
    （见 ``classic._prefer_subtitle``），所以只看 ``release.subtitle`` 必然
    漏掉另一维；``release.raw`` 是原始标题，兜底扫它才能两维都拿全。
    """
    language, style = parse_subtitle_tags(release.subtitle)
    if language is not None and style is not None:
        return language, style
    raw_language, raw_style = parse_subtitle_tags(release.raw)
    return language or raw_language, style or raw_style
