# Parser Settings

The parser extracts structured metadata such as title, season, episode and subgroup from RSS item titles.

::: tip
Since v3.1, the source parser for each RSS feed is configured when adding or editing that feed. This page controls the global switch, title parser, metadata language, default preferences and filters.
:::

## WebUI

- **Enable**: enables RSS parsing.
- **Title parser**: `Classic parser (Stable)` preserves the existing behavior. `Universal parser (Preview)` adds episode ranges, OVAs, movies and mixed collections. The engines never silently fall back to each other.
- **Metadata language**: language used for TMDB lookups, deciding which language the official title and poster come back in. Supported values are `zh`, `jp` and `en`. **Unrelated to subtitle language.**
- **Exclude**: global filter rules. Plain strings and regular expressions are supported.
- **Default preferred group / resolution / subtitle language / subtitle style**: the preference starting point written into an anime when it is first added. Leave blank for no default. See [Default release preferences](#default-release-preferences).

## `config.json`

Section: `rss_parser`

| Key | Description | Type | WebUI field | Default |
| --- | --- | --- | --- | --- |
| `enable` | Enable RSS parser | boolean | Enable | `true` |
| `engine` | Title parser: `classic` or `tokenizer` | string | Title parser | `classic` |
| `filter` | Global filters | string array | Exclude | `["720", "\\d+-\\d+"]` |
| `language` | TMDB metadata language | string | Metadata language | `zh` |
| `default_preferred_group` | Default preferred group for new anime | string | Default preferred group | `""` |
| `default_preferred_resolution` | Default preferred resolution for new anime | string | Default preferred resolution | `""` |
| `default_preferred_subtitle_language` | Default preferred subtitle language for new anime | string | Default preferred subtitle language | `""` |
| `default_preferred_subtitle_style` | Default preferred subtitle style for new anime | string | Default preferred subtitle style | `""` |

## Default release preferences

A subgroup often publishes the same episode several times over (for example "Simplified + Traditional softsubbed", "Traditional hardsubbed" and "Simplified hardsubbed") with an identical group name and resolution. All of those duplicates get downloaded, then collide during renaming because they resolve to the same target filename.

The four release preferences pick which one to keep. On an RSS refresh, candidates for the same episode are scored by how many preferences they match, and only the highest scorer is kept. All four carry equal weight, so you decide how many to fill in; with none set, no dedup happens at all and the behavior is unchanged.

### Values

`default_preferred_subtitle_language` and `default_preferred_subtitle_style` are normalized enums, not free text:

| Subtitle language | Meaning | Recognized spellings (examples) |
| --- | --- | --- |
| `chs` | Simplified Chinese | 简体, 简中, 简日双语, CHS, GB, JPSC |
| `cht` | Traditional Chinese | 繁体, 繁中, 繁日, CHT, Big5, JPTC |
| `chs_cht` | Simplified + Traditional | 简繁, 繁简, CHS&CHT |
| `jpn` | Japanese | 日语, 日文, JPN |
| `eng` | English | 英语, 英文, ENG, VOSTFR |

| Subtitle style | Meaning | Recognized spellings (examples) |
| --- | --- | --- |
| `embedded` | Hardsubbed | 内嵌, 硬字幕 |
| `muxed` | Softsubbed | 内封 |
| `external` | External file | 外挂, 外置 |

When a title carries no recognizable marker, that dimension simply scores nothing — it never causes an episode to be missed.

### Scope

::: warning
These four are **defaults applied when an anime is first added**. They are written into the anime at that moment only; changing them later leaves every existing anime untouched.
:::

Anime already in the database are adjusted one by one in their own bangumi rule.
