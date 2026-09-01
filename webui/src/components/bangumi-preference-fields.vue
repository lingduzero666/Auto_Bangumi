<script lang="ts" setup>
/**
 * 四个发布偏好（字幕组 / 分辨率 / 字幕语言 / 字幕压制）的输入行。
 *
 * ab-add-rss 的确认弹窗与番剧规则编辑共用同一组语义：设置后，同一集出现
 * 多个版本时只保留最贴合偏好的那个。字幕语言与压制是规范化枚举，不开放
 * 自由输入——后端按归一后的值匹配，手填的字符串落不到枚举上只会静默失效；
 * 字幕组与分辨率则保持可自由填写，字幕组名和冷门分辨率无法穷举。
 */
import { NSelect } from 'naive-ui';
import type { BangumiRule } from '#/bangumi';

const rule = defineModel<BangumiRule>('rule', { required: true });

const { t } = useMyI18n();

// 常见分辨率作预设，filterable+tag 允许输入任意值
const resolutionOptions = ['2160p', '1080p', '720p'].map((r) => ({
  label: r,
  value: r,
}));

const subtitleLanguageOptions = computed(() => [
  { label: t('homepage.rule.subtitle_language_chs'), value: 'chs' },
  { label: t('homepage.rule.subtitle_language_cht'), value: 'cht' },
  { label: t('homepage.rule.subtitle_language_chs_cht'), value: 'chs_cht' },
  { label: t('homepage.rule.subtitle_language_jpn'), value: 'jpn' },
  { label: t('homepage.rule.subtitle_language_eng'), value: 'eng' },
]);

const subtitleStyleOptions = computed(() => [
  { label: t('homepage.rule.subtitle_style_embedded'), value: 'embedded' },
  { label: t('homepage.rule.subtitle_style_muxed'), value: 'muxed' },
  { label: t('homepage.rule.subtitle_style_external'), value: 'external' },
]);
</script>

<template>
  <div class="advanced-row">
    <label class="advanced-label">{{
      $t('homepage.rule.preferred_group')
    }}</label>
    <div class="advanced-control">
      <ab-input
        :model-value="rule.preferred_group ?? ''"
        type="text"
        class="preference-control"
        placeholder="ANi"
        :aria-label="$t('homepage.rule.preferred_group')"
        @update:model-value="rule.preferred_group = String($event)"
      />
    </div>
  </div>

  <div class="advanced-row">
    <label class="advanced-label">{{
      $t('homepage.rule.preferred_resolution')
    }}</label>
    <div class="advanced-control">
      <NSelect
        v-model:value="rule.preferred_resolution"
        :options="resolutionOptions"
        class="preference-control"
        size="small"
        clearable
        filterable
        tag
        :placeholder="$t('homepage.rule.auto_detect')"
        :aria-label="$t('homepage.rule.preferred_resolution')"
      />
    </div>
  </div>

  <div class="advanced-row">
    <label class="advanced-label">{{
      $t('homepage.rule.preferred_subtitle_language')
    }}</label>
    <div class="advanced-control">
      <NSelect
        v-model:value="rule.preferred_subtitle_language"
        :options="subtitleLanguageOptions"
        class="preference-control"
        size="small"
        clearable
        :placeholder="$t('homepage.rule.auto_detect')"
        :aria-label="$t('homepage.rule.preferred_subtitle_language')"
      />
    </div>
  </div>

  <div class="advanced-row">
    <label class="advanced-label">{{
      $t('homepage.rule.preferred_subtitle_style')
    }}</label>
    <div class="advanced-control">
      <NSelect
        v-model:value="rule.preferred_subtitle_style"
        :options="subtitleStyleOptions"
        class="preference-control"
        size="small"
        clearable
        :placeholder="$t('homepage.rule.auto_detect')"
        :aria-label="$t('homepage.rule.preferred_subtitle_style')"
      />
    </div>
  </div>
</template>

<style lang="scss" scoped>
.advanced-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 32px;
}

.advanced-label {
  flex-shrink: 0;
  font-size: 13px;
  font-weight: 500;
  color: var(--color-text-secondary);
  line-height: 32px;
}

.advanced-control {
  display: flex;
  justify-content: flex-end;
}

.preference-control {
  width: 160px;
}
</style>
