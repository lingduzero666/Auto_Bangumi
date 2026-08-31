# Parser Settings

The parser extracts structured metadata such as title, season, episode and subgroup from RSS item titles.

::: tip
Since v3.1, the source parser for each RSS feed is configured when adding or editing that feed. This page controls the global switch, title parser, language and filters.
:::

## WebUI

- **Enable**: enables RSS parsing.
- **Title parser**: `Classic parser (Stable)` preserves the existing behavior. `Universal parser (Preview)` adds episode ranges, OVAs, movies and mixed collections. The engines never silently fall back to each other.
- **Language**: preferred parser language. Supported values are `zh`, `jp` and `en`.
- **Air weekday source**: which source the calendar asks first for an anime's air weekday. `Bangumi.tv first` keeps the existing behaviour; `Parsers first` follows each subscription's own parser type — `mikan` and `mix` read the air day written on the Mikan bangumi page, `tmdb` derives it from air dates, other types have no parser source. Either way, the other source still serves as a fallback for anime the preferred one cannot resolve. Prefer parsers when bgm.tv is unreachable.
- **Exclude**: global filter rules. Plain strings and regular expressions are supported.

## `config.json`

Section: `rss_parser`

| Key | Description | Type | WebUI field | Default |
| --- | --- | --- | --- | --- |
| `enable` | Enable RSS parser | boolean | Enable | `true` |
| `engine` | Title parser: `classic` or `tokenizer` | string | Title parser | `classic` |
| `filter` | Global filters | string array | Exclude | `["720", "\\d+-\\d+"]` |
| `language` | Parser language | string | Language | `zh` |
| `weekday_source` | Air weekday source: `bgm` or `parser` | string | Air weekday source | `bgm` |
