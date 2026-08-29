"""Tests for the custom filename template renderer and validator."""

import pytest

from module.manager.rename_template import (
    DEFAULT_EPISODE_TEMPLATE,
    DEFAULT_MOVIE_TEMPLATE,
    build_fields,
    format_number,
    render_template,
    validate_template,
)
from module.models.config import BangumiManage


def _fields(
    *,
    parser_title: str = "My Anime",
    official_title: str | None = "官方名",
    folder_name: str = "My Anime (2024)",
    season: int = 1,
    episode: int | float = 5,
    group: str | None = "TestGroup",
    year: str | None = "2024",
    resolution: str | None = "1080p",
    source: str | None = "Baha",
    subtitle: str | None = "CHT",
    language: str | None = None,
) -> dict[str, object]:
    return build_fields(
        parser_title=parser_title,
        official_title=official_title,
        folder_name=folder_name,
        season=season,
        episode=episode,
        group=group,
        year=year,
        resolution=resolution,
        source=source,
        subtitle=subtitle,
        language=language,
    )


class TestDefaultsMatchConfig:
    """配置默认值与模板模块的常量必须一致。

    ``models/config.py`` 里写的是字面量而非 import——``module.manager.__init__``
    会拉起 renamer → module.conf → models.config，成环。这条断言就是那份
    重复的守卫。
    """

    def test_episode_default_matches(self):
        assert BangumiManage().rename_template == DEFAULT_EPISODE_TEMPLATE

    def test_movie_default_matches(self):
        assert BangumiManage().movie_rename_template == DEFAULT_MOVIE_TEMPLATE


class TestFormatNumber:
    @pytest.mark.parametrize(
        ("value", "width", "expected"),
        (
            (5, 0, "5"),
            (5, 2, "05"),
            (5, 3, "005"),
            (12, 2, "12"),
            (100, 2, "100"),
            (12.5, 0, "12.5"),
            (12.5, 2, "12.5"),
            (5.5, 2, "05.5"),
            (1, 2, "01"),
        ),
    )
    def test_widths(self, value, width, expected):
        assert format_number(value, width) == expected

    def test_integral_float_loses_the_decimal(self):
        """12.0 必须渲染成 12，否则会和整数集分裂成两个文件名。"""
        assert format_number(12.0, 2) == "12"


class TestRender:
    def test_default_episode_template(self):
        """默认模板**刻意不等价于 pn**：用数据库里的 official_title 和字幕组，
        命名质量更高。template 是 opt-in 模式，不会波及没切过去的用户。"""
        rendered = render_template(DEFAULT_EPISODE_TEMPLATE, _fields(), suffix=".mkv")
        assert rendered == "[TestGroup] 官方名 S01E05.mkv"

    def test_default_movie_template(self):
        rendered = render_template(DEFAULT_MOVIE_TEMPLATE, _fields(), suffix=".mkv")
        assert rendered == "[TestGroup] 官方名 (2024).mkv"

    def test_default_episode_template_without_a_bangumi_row(self):
        """种子匹配不到 Bangumi 行时 official_title / group 都为空——默认模板
        会退化成只剩集号。已知短板，见 PR 说明。"""
        fields = _fields(official_title=None, group=None)
        rendered = render_template(DEFAULT_EPISODE_TEMPLATE, fields, suffix=".mkv")
        assert rendered == "S01E05.mkv"

    def test_bare_number_is_unpadded(self):
        """不写 :N 就是原值——这正是不需要 *_nopad 变量的原因。"""
        rendered = render_template(
            "{{parser_title}} S{{season}}E{{episode}}", _fields(), suffix=".mkv"
        )
        assert rendered == "My Anime S1E5.mkv"

    def test_pad_width_is_honoured(self):
        rendered = render_template("E{{episode:3}}", _fields(), suffix=".mkv")
        assert rendered == "E005.mkv"

    def test_half_episode_pads_only_the_integer_part(self):
        fields = _fields(episode=5.5)
        assert render_template("E{{episode:2}}", fields, suffix=".mkv") == "E05.5.mkv"

    def test_official_title(self):
        rendered = render_template("{{official_title}}", _fields(), suffix=".mkv")
        assert rendered == "官方名.mkv"

    def test_folder_name(self):
        rendered = render_template("{{folder_name}}", _fields(), suffix=".mkv")
        assert rendered == "My Anime (2024).mkv"

    def test_all_variables(self):
        template = (
            "{{folder_name}} S{{season:2}}E{{episode:2}} "
            "[{{group}}][{{resolution}}][{{source}}][{{subtitle}}][{{year}}]"
        )
        assert render_template(template, _fields(), suffix=".mkv") == (
            "My Anime (2024) S01E05 [TestGroup][1080p][Baha][CHT][2024].mkv"
        )

    def test_suffix_appended_when_absent(self):
        assert (
            render_template("{{parser_title}}", _fields(), suffix=".mkv")
            == "My Anime.mkv"
        )

    def test_suffix_not_doubled(self):
        """标题恰好以扩展名结尾时不能补第二次。"""
        fields = _fields(parser_title="Weird.mkv")
        assert render_template("{{parser_title}}", fields, suffix=".mkv") == "Weird.mkv"

    def test_empty_variable_cleans_up_brackets(self):
        """字幕组为空时不能留下 "[]"。"""
        fields = _fields(group=None)
        rendered = render_template(
            "{{parser_title}} [{{group}}] S{{season:2}}E{{episode:2}}",
            fields,
            suffix=".mkv",
        )
        assert rendered == "My Anime S01E05.mkv"

    def test_empty_variable_cleans_trailing_separator(self):
        fields = _fields(group=None)
        assert render_template(
            "{{parser_title}} - {{group}}", fields, suffix=".mkv"
        ) == ("My Anime.mkv")

    def test_leading_separator_is_cleaned(self):
        fields = _fields(group=None)
        assert render_template(
            "{{group}} - {{parser_title}}", fields, suffix=".mkv"
        ) == ("My Anime.mkv")

    def test_title_own_underscores_survive(self):
        """悬空连接符的清理必须要求相邻空格，否则会咬掉标题自带的下划线。"""
        fields = _fields(parser_title="_Underscore_")
        assert render_template("{{parser_title}}", fields, suffix=".mkv") == (
            "_Underscore_.mkv"
        )

    def test_path_separators_in_values_become_spaces(self):
        """标题里带斜杠会把文件重命名进子目录——保存时的模板校验拦不住值。"""
        fields = _fields(parser_title="Fate/stay night")
        assert render_template("{{parser_title}}", fields, suffix=".mkv") == (
            "Fate stay night.mkv"
        )

    def test_reserved_characters_other_than_separators_are_kept(self):
        """只清理分隔符，冒号等保留字符原样保留——与 #721 的既定立场一致。"""
        fields = _fields(parser_title="Re:Zero")
        assert render_template("{{parser_title}}", fields, suffix=".mkv") == (
            "Re:Zero.mkv"
        )

    def test_unknown_variable_renders_empty(self):
        assert render_template(
            "{{parser_title}}{{nope}}", _fields(), suffix=".mkv"
        ) == ("My Anime.mkv")

    def test_whitespace_inside_braces_is_tolerated(self):
        assert render_template("{{ parser_title }}", _fields(), suffix=".mkv") == (
            "My Anime.mkv"
        )

    def test_all_variables_empty_renders_only_suffix(self):
        """全空时调用方要能识别出「渲染结果只剩扩展名」并放弃重命名。"""
        fields = build_fields(parser_title="")
        assert render_template("{{parser_title}}{{group}}", fields, suffix=".mkv") == (
            ".mkv"
        )


class TestValidate:
    def test_empty_is_allowed(self):
        assert validate_template("") is None
        assert validate_template("   ") is None

    def test_valid_template(self):
        assert validate_template("{{parser_title}} S{{season:2}}E{{episode:2}}") is None

    @pytest.mark.parametrize("bad", ("{{parser_title}}/{{episode}}", "a\\b{{episode}}"))
    def test_path_separators_rejected(self, bad):
        assert "路径分隔符" in (validate_template(bad) or "")

    def test_unknown_variable_rejected(self):
        error = validate_template("{{parser_title}} {{bogus}}")
        assert error is not None and "bogus" in error

    def test_old_names_are_rejected(self):
        """改名后旧变量必须明确报错，而不是静默渲染成空。"""
        for old in ("{{title}}", "{{bangumi_name}}", "{{episode_nopad}}"):
            error = validate_template(f"{old} {{{{episode}}}}")
            assert error is not None and "未知变量" in error

    def test_pad_width_on_a_string_is_rejected(self):
        error = validate_template("{{parser_title:2}} {{episode}}")
        assert error is not None and "不是数字" in error

    @pytest.mark.parametrize("width", ("0", "5"))
    def test_out_of_range_pad_width_is_rejected(self, width):
        error = validate_template("{{episode:%s}}" % width)
        assert error is not None and "补零宽度" in error

    def test_episode_required_when_asked(self):
        """剧集模板缺集数变量会让整个季度合集渲染成同一个名字、互相覆盖。"""
        error = validate_template("{{parser_title}}", require_episode=True)
        assert error is not None and "episode" in error

    @pytest.mark.parametrize("good", ("{{parser_title}} {{episode}}", "{{episode:2}}"))
    def test_episode_requirement_satisfied(self, good):
        assert validate_template(good, require_episode=True) is None

    def test_movie_template_does_not_require_episode(self):
        assert validate_template("{{parser_title}} ({{year}})") is None
