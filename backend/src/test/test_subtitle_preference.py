"""字幕语言/压制偏好：标签规范化与新番剧默认偏好的落库。"""

from unittest.mock import patch

import pytest

from module.conf import settings
from module.parser.analyser.selector import parse_configured_release_title
from module.parser.subtitle_tags import (
    SUBTITLE_LANGUAGE_CHS,
    SUBTITLE_LANGUAGE_CHS_CHT,
    SUBTITLE_LANGUAGE_CHT,
    SUBTITLE_LANGUAGE_ENG,
    SUBTITLE_STYLE_EMBEDDED,
    SUBTITLE_STYLE_EXTERNAL,
    SUBTITLE_STYLE_MUXED,
    parse_subtitle_tags,
    release_subtitle_tags,
)
from module.rss.analyser import apply_default_preferences
from test.factories import make_bangumi


class TestParseSubtitleTags:
    """把字幕组千奇百怪的写法归一到有限枚举。"""

    @pytest.mark.parametrize(
        "text, language, style",
        [
            # 用户实际遇到的一集三版：同组同分辨率，只有字幕标签不同
            ("[桜都字幕组] 番剧 [08][1080P][简繁内封]", "chs_cht", "muxed"),
            ("[桜都字幕组] 番剧 [08][1080P][繁体内嵌]", "cht", "embedded"),
            ("[桜都字幕组] 番剧 [08][1080P][简体内嵌]", "chs", "embedded"),
            # 英文短码
            ("[Sakurato] Show [01][1080p][CHS][内嵌]", "chs", "embedded"),
            ("[ANi] Show - 05 [1080P][Baha][WEB-DL][CHT]", "cht", None),
            ("[Group] Show [01][1080p][GB_MP4]", "chs", None),
            ("[Group] Show [01][1080p][BIG5][内封]", "cht", "muxed"),
            # 中日对照按其中的中文归类，日文只是伴随
            ("[喵萌奶茶屋] Show [01][1080p][简日双语]", "chs", None),
            ("[北宇治字幕组] Show [03][1080p][繁日内嵌]", "cht", "embedded"),
            ("[Group] Show [01][1080p][外挂字幕][简体]", "chs", "external"),
            ("[Group] Show [01][1080p][ENG]", "eng", None),
        ],
    )
    def test_recognizes_common_tags(self, text, language, style):
        assert parse_subtitle_tags(text) == (language, style)

    @pytest.mark.parametrize(
        "text",
        [
            "[Group] Show [01][1080p]",  # 完全没有字幕标签
            "[Group] Show [01][1080p][中文字幕]",  # 有字幕但没说简繁
        ],
    )
    def test_unknown_tags_stay_none(self, text):
        """认不出就返回 None——宁可不去重，也不能猜错跳过用户要的版本。"""
        assert parse_subtitle_tags(text) == (None, None)

    def test_double_language_wins_over_single(self):
        """「简繁」必须整体命中，不能被「简」或「繁」抢先。"""
        language, _ = parse_subtitle_tags("[Group] Show [01][简繁内封]")
        assert language == SUBTITLE_LANGUAGE_CHS_CHT
        assert language != SUBTITLE_LANGUAGE_CHS

    def test_empty_text_is_safe(self):
        assert parse_subtitle_tags(None) == (None, None)
        assert parse_subtitle_tags("") == (None, None)

    def test_all_styles_recognized(self):
        assert parse_subtitle_tags("内嵌")[1] == SUBTITLE_STYLE_EMBEDDED
        assert parse_subtitle_tags("内封")[1] == SUBTITLE_STYLE_MUXED
        assert parse_subtitle_tags("外挂")[1] == SUBTITLE_STYLE_EXTERNAL


class TestReleaseSubtitleTags:
    def test_falls_back_to_raw_title_for_missing_dimension(self):
        """解析器对拆在多个方括号里的标签只留一个，另一维要靠扫原标题补回。"""
        release = parse_configured_release_title(
            "[Sakurato] Show [01][1080p][AVC-8bit AAC][CHS][内嵌]"
        )
        assert release is not None
        assert release_subtitle_tags(release) == (
            SUBTITLE_LANGUAGE_CHS,
            SUBTITLE_STYLE_EMBEDDED,
        )

    def test_reads_both_dimensions_from_one_tag(self):
        release = parse_configured_release_title(
            "[桜都字幕组] 番剧 / Show [08][1080P][繁体内嵌]"
        )
        assert release is not None
        assert release_subtitle_tags(release) == (
            SUBTITLE_LANGUAGE_CHT,
            SUBTITLE_STYLE_EMBEDDED,
        )

    def test_no_subtitle_tag_gives_none(self):
        release = parse_configured_release_title("[Group] Show - 01 [1080p].mkv")
        assert release is not None
        assert release_subtitle_tags(release) == (None, None)

    def test_english_tag_recognized(self):
        release = parse_configured_release_title("[Group] Show - 01 [1080p][ENG].mkv")
        assert release is not None
        assert release_subtitle_tags(release)[0] == SUBTITLE_LANGUAGE_ENG


class TestApplyDefaultPreferences:
    """全局默认偏好在新番剧入库那一刻固化进番剧行。"""

    def test_empty_defaults_change_nothing(self):
        """默认全空＝维持升级前行为，不开启任何去重。"""
        bangumi = make_bangumi()
        apply_default_preferences(bangumi)
        assert bangumi.preferred_group is None
        assert bangumi.preferred_resolution is None
        assert bangumi.preferred_subtitle_language is None
        assert bangumi.preferred_subtitle_style is None

    def test_defaults_are_written_into_new_bangumi(self):
        bangumi = make_bangumi()
        with (
            patch.object(settings.rss_parser, "default_preferred_group", "桜都字幕组"),
            patch.object(settings.rss_parser, "default_preferred_resolution", "1080p"),
            patch.object(
                settings.rss_parser, "default_preferred_subtitle_language", "chs"
            ),
            patch.object(
                settings.rss_parser, "default_preferred_subtitle_style", "embedded"
            ),
        ):
            apply_default_preferences(bangumi)

        assert bangumi.preferred_group == "桜都字幕组"
        assert bangumi.preferred_resolution == "1080p"
        assert bangumi.preferred_subtitle_language == "chs"
        assert bangumi.preferred_subtitle_style == "embedded"

    def test_existing_values_are_not_overwritten(self):
        """调用方已经定好的值优先，默认值只填空位。"""
        bangumi = make_bangumi(
            preferred_subtitle_language="cht", preferred_group="GroupA"
        )
        with (
            patch.object(settings.rss_parser, "default_preferred_group", "GroupB"),
            patch.object(
                settings.rss_parser, "default_preferred_subtitle_language", "chs"
            ),
            patch.object(
                settings.rss_parser, "default_preferred_subtitle_style", "muxed"
            ),
        ):
            apply_default_preferences(bangumi)

        assert bangumi.preferred_group == "GroupA"
        assert bangumi.preferred_subtitle_language == "cht"
        # 空位仍然被默认值填上
        assert bangumi.preferred_subtitle_style == "muxed"

    def test_blank_default_stays_null_not_empty_string(self):
        """空字符串统一写成 None，「没设置」在库里只有 NULL 一种表示。"""
        bangumi = make_bangumi()
        with patch.object(settings.rss_parser, "default_preferred_group", ""):
            apply_default_preferences(bangumi)
        assert bangumi.preferred_group is None
