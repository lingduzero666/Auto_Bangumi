"""Tests for the custom filename template renderer and validator."""

import pytest

from module.manager.rename_template import (
    DEFAULT_EPISODE_TEMPLATE,
    DEFAULT_MOVIE_TEMPLATE,
    build_fields,
    format_episode,
    format_season,
    render_template,
    validate_template,
)
from module.models.config import BangumiManage


def _fields(
    *,
    title: str = "My Anime",
    bangumi_name: str = "My Anime (2024)",
    season: int = 1,
    episode: int | float = 5,
    group: str | None = "TestGroup",
    year: str | None = "2024",
    resolution: str | None = "1080p",
    source: str | None = "Baha",
    subtitle: str | None = "CHT",
    language: str | None = None,
) -> dict[str, str]:
    return build_fields(
        title=title,
        bangumi_name=bangumi_name,
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


class TestFormatting:
    @pytest.mark.parametrize(
        ("episode", "expected"),
        ((1, "01"), (5, "05"), (9, "09"), (10, "10"), (100, "100"), (12.5, "12.5")),
    )
    def test_format_episode(self, episode, expected):
        assert format_episode(episode) == expected

    def test_integral_float_loses_the_decimal(self):
        """12.0 必须渲染成 12，否则会和整数集分裂成两个文件名。"""
        assert format_episode(12.0) == "12"

    @pytest.mark.parametrize(("season", "expected"), ((1, "01"), (10, "10")))
    def test_format_season(self, season, expected):
        assert format_season(season) == expected

    def test_nopad_variants(self):
        fields = _fields(season=1, episode=5)
        assert fields["season"] == "01"
        assert fields["episode"] == "05"
        assert fields["season_nopad"] == "1"
        assert fields["episode_nopad"] == "5"

    def test_nopad_keeps_half_episode(self):
        assert _fields(episode=12.5)["episode_nopad"] == "12.5"


class TestRender:
    def test_default_episode_template_equals_pn(self):
        """默认模板必须逐字节复现 pn 的输出，否则升级会静默改名整个库。"""
        rendered = render_template(DEFAULT_EPISODE_TEMPLATE, _fields(), suffix=".mkv")
        assert rendered == "My Anime S01E05.mkv"

    def test_default_movie_template_equals_movie_branch(self):
        rendered = render_template(DEFAULT_MOVIE_TEMPLATE, _fields(), suffix=".mkv")
        assert rendered == "My Anime.mkv"

    def test_all_variables(self):
        template = (
            "{{bangumi_name}} S{{season}}E{{episode}} "
            "[{{group}}][{{resolution}}][{{source}}][{{subtitle}}][{{year}}]"
        )
        assert render_template(template, _fields(), suffix=".mkv") == (
            "My Anime (2024) S01E05 [TestGroup][1080p][Baha][CHT][2024].mkv"
        )

    def test_suffix_appended_when_absent(self):
        assert render_template("{{title}}", _fields(), suffix=".mkv") == "My Anime.mkv"

    def test_suffix_not_doubled(self):
        """标题恰好以扩展名结尾时不能补第二次。"""
        fields = _fields(title="Weird.mkv")
        assert render_template("{{title}}", fields, suffix=".mkv") == "Weird.mkv"

    def test_empty_variable_cleans_up_brackets(self):
        """字幕组为空时不能留下 "[]"。"""
        fields = _fields(group=None)
        rendered = render_template(
            "{{title}} [{{group}}] S{{season}}E{{episode}}", fields, suffix=".mkv"
        )
        assert rendered == "My Anime S01E05.mkv"

    def test_empty_variable_cleans_trailing_separator(self):
        fields = _fields(group=None)
        assert render_template("{{title}} - {{group}}", fields, suffix=".mkv") == (
            "My Anime.mkv"
        )

    def test_leading_separator_is_cleaned(self):
        fields = _fields(group=None)
        assert render_template("{{group}} - {{title}}", fields, suffix=".mkv") == (
            "My Anime.mkv"
        )

    def test_title_own_underscores_survive(self):
        """悬空连接符的清理必须要求相邻空格，否则会咬掉标题自带的下划线。"""
        fields = _fields(title="_Underscore_")
        assert render_template("{{title}}", fields, suffix=".mkv") == (
            "_Underscore_.mkv"
        )

    def test_title_own_trailing_dash_survives(self):
        fields = _fields(title="Anime-")
        assert render_template("{{title}}", fields, suffix=".mkv") == "Anime-.mkv"

    def test_path_separators_in_values_become_spaces(self):
        """标题里带斜杠会把文件重命名进子目录——保存时的模板校验拦不住值。"""
        fields = _fields(title="Fate/stay night")
        assert render_template("{{title}}", fields, suffix=".mkv") == (
            "Fate stay night.mkv"
        )

    def test_reserved_characters_other_than_separators_are_kept(self):
        """只清理分隔符，冒号等保留字符原样保留——与 #721 的既定立场一致。"""
        fields = _fields(title="Re:Zero")
        assert render_template("{{title}}", fields, suffix=".mkv") == "Re:Zero.mkv"

    def test_unknown_variable_renders_empty(self):
        assert render_template("{{title}}{{nope}}", _fields(), suffix=".mkv") == (
            "My Anime.mkv"
        )

    def test_whitespace_inside_braces_is_tolerated(self):
        assert render_template("{{ title }}", _fields(), suffix=".mkv") == (
            "My Anime.mkv"
        )

    def test_all_variables_empty_renders_only_suffix(self):
        """全空时调用方要能识别出「渲染结果只剩扩展名」并放弃重命名。"""
        fields = build_fields(title="", bangumi_name="", season=1, episode=1)
        assert render_template("{{title}}{{group}}", fields, suffix=".mkv") == ".mkv"


class TestValidate:
    def test_empty_is_allowed(self):
        assert validate_template("") is None
        assert validate_template("   ") is None

    def test_valid_template(self):
        assert validate_template("{{title}} S{{season}}E{{episode}}") is None

    @pytest.mark.parametrize("bad", ("{{title}}/{{episode}}", "a\\b{{episode}}"))
    def test_path_separators_rejected(self, bad):
        assert "路径分隔符" in (validate_template(bad) or "")

    def test_unknown_variable_rejected(self):
        error = validate_template("{{title}} {{bogus}}")
        assert error is not None and "bogus" in error

    def test_episode_required_when_asked(self):
        """剧集模板缺集数变量会让整个季度合集渲染成同一个名字、互相覆盖。"""
        error = validate_template("{{title}}", require_episode=True)
        assert error is not None and "episode" in error

    @pytest.mark.parametrize("good", ("{{title}} {{episode}}", "{{episode_nopad}}"))
    def test_episode_requirement_satisfied(self, good):
        assert validate_template(good, require_episode=True) is None

    def test_movie_template_does_not_require_episode(self):
        assert validate_template("{{title}} ({{year}})") is None
