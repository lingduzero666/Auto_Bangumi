<script lang="ts" setup>
import type { RssParser, RssParserLang } from '#/config';
import type { SelectItem, SettingItem } from '#/components';

const { t } = useMyI18n();
const { getSettingGroup } = useConfigStore();

const parser = getSettingGroup('rss_parser');

const langs: RssParserLang = ['zh', 'en', 'jp'];
const engineOptions = computed<SelectItem[]>(() => [
  {
    id: 1,
    label: t('config.parser_set.engine_classic'),
    value: 'classic',
  },
  {
    id: 2,
    label: t('config.parser_set.engine_tokenizer'),
    value: 'tokenizer',
  },
]);
const weekdaySourceOptions = computed<SelectItem[]>(() => [
  {
    id: 1,
    label: t('config.parser_set.weekday_source_bgm'),
    value: 'bgm',
  },
  {
    id: 2,
    label: t('config.parser_set.weekday_source_parser'),
    value: 'parser',
  },
]);

// 每个偏好下拉的第一项：留空＝不设默认值，保持「下载所有版本」的旧行为
function unsetOption(): SelectItem {
  return {
    id: 0,
    label: t('config.parser_set.preference_unset'),
    value: '',
  };
}

const preferredResolutionOptions = computed<SelectItem[]>(() => [
  unsetOption(),
  { id: 1, label: '2160p', value: '2160p' },
  { id: 2, label: '1080p', value: '1080p' },
  { id: 3, label: '720p', value: '720p' },
]);

const subtitleLanguageOptions = computed<SelectItem[]>(() => [
  unsetOption(),
  { id: 1, label: t('config.parser_set.subtitle_language_chs'), value: 'chs' },
  { id: 2, label: t('config.parser_set.subtitle_language_cht'), value: 'cht' },
  {
    id: 3,
    label: t('config.parser_set.subtitle_language_chs_cht'),
    value: 'chs_cht',
  },
  { id: 4, label: t('config.parser_set.subtitle_language_jpn'), value: 'jpn' },
  { id: 5, label: t('config.parser_set.subtitle_language_eng'), value: 'eng' },
]);

const subtitleStyleOptions = computed<SelectItem[]>(() => [
  unsetOption(),
  {
    id: 1,
    label: t('config.parser_set.subtitle_style_embedded'),
    value: 'embedded',
  },
  { id: 2, label: t('config.parser_set.subtitle_style_muxed'), value: 'muxed' },
  {
    id: 3,
    label: t('config.parser_set.subtitle_style_external'),
    value: 'external',
  },
]);

const items: SettingItem<RssParser>[] = [
  {
    configKey: 'language',
    label: () => t('config.parser_set.language'),
    description: t('config.parser_set.language_hint'),
    type: 'select',
    prop: {
      items: langs,
    },
  },
  {
    configKey: 'filter',
    label: () => t('config.parser_set.exclude'),
    type: 'dynamic-tags',
  },
];
</script>

<template>
  <ab-fold-panel :title="$t('config.parser_set.title')">
    <div space-y-8>
      <ab-setting
        v-model:data="parser.enable"
        :label="() => t('config.parser_set.enable')"
        type="switch"
      ></ab-setting>
      <ab-setting
        v-model:data="parser.engine"
        :label="() => t('config.parser_set.engine')"
        :description="t('config.parser_set.engine_hint')"
        type="select"
        :prop="{ items: engineOptions }"
      ></ab-setting>
      <ab-setting
        v-model:data="parser.weekday_source"
        :label="() => t('config.parser_set.weekday_source')"
        :description="t('config.parser_set.weekday_source_hint')"
        type="select"
        :prop="{ items: weekdaySourceOptions }"
      ></ab-setting>
      <ab-setting
        v-for="i in items"
        :key="i.configKey"
        v-bind="i"
        v-model:data="parser[i.configKey]"
      ></ab-setting>
      <ab-setting
        v-model:data="parser.default_preferred_group"
        :label="() => t('config.parser_set.default_preferred_group')"
        :description="t('config.parser_set.preference_hint')"
        type="input"
        :prop="{ placeholder: 'ANi' }"
      ></ab-setting>
      <ab-setting
        v-model:data="parser.default_preferred_resolution"
        :label="() => t('config.parser_set.default_preferred_resolution')"
        type="select"
        :prop="{ items: preferredResolutionOptions }"
      ></ab-setting>
      <ab-setting
        v-model:data="parser.default_preferred_subtitle_language"
        :label="
          () => t('config.parser_set.default_preferred_subtitle_language')
        "
        type="select"
        :prop="{ items: subtitleLanguageOptions }"
      ></ab-setting>
      <ab-setting
        v-model:data="parser.default_preferred_subtitle_style"
        :label="() => t('config.parser_set.default_preferred_subtitle_style')"
        type="select"
        :prop="{ items: subtitleStyleOptions }"
      ></ab-setting>
    </div>
  </ab-fold-panel>
</template>
