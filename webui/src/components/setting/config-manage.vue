<script lang="ts" setup>
import { useDebounceFn } from '@vueuse/core';
import type {
  BangumiManage,
  RenameMethod,
  RenamePreview,
  RevisionConflictPolicy,
} from '#/config';
import type { SelectItem, SettingItem } from '#/components';

const { t } = useMyI18n();
const { getSettingGroup } = useConfigStore();

const manage = getSettingGroup('bangumi_manage');
const renameMethod: RenameMethod = [
  'normal',
  'pn',
  'advance',
  'none',
  'template',
];

// 变量名与占位符是代码字面量，**不能进 i18n**：一来翻译它们会直接把功能翻坏，
// 二来 vue-i18n 的插值语法就是 {xxx}，把 {{title}} 放进 JSON 会被消息编译器
// 当成嵌套占位符而编译失败（error code 9）。
// 同理 Vue 模板里也不能直接写 {{title}}，只能从 script 传字符串进去。
// 季度/集数写成 {{season:2}} 的形式带补零宽度，不带 :N 就是原值
const TEMPLATE_VARIABLES = [
  'parser_title',
  'official_title',
  'folder_name',
  'season:2',
  'episode:2',
  'group',
  'year',
  'resolution',
  'source',
  'subtitle',
];
// 文件夹在添加种子时生成，那一刻没有解析过的文件——parser_title / episode
// 拿不到，folder_name 也不能自我引用，所以是独立的一套更小的变量
// season 刻意不给：gen_save_path 已经单独建了 "Season N" 子目录
const FOLDER_TEMPLATE_VARIABLES = [
  'official_title',
  'year',
  'group',
  'resolution',
  'source',
  'subtitle',
];
function asPlaceholders(names: string[]) {
  return names.map((name) => `{{${name}}}`).join(' ');
}
const variableList = asPlaceholders(TEMPLATE_VARIABLES);
const folderVariableList = asPlaceholders(FOLDER_TEMPLATE_VARIABLES);
const episodePlaceholder =
  '[{{group}}] {{official_title}} S{{season:2}}E{{episode:2}}';
const moviePlaceholder = '[{{group}}] {{official_title}} ({{year}})';
const folderPlaceholder = '{{official_title}} ({{year}})';

// 示例由后端渲染：模板求值逻辑在 renamer 里，前端复刻必然漂移（补零规则、
// 半集 12.5、剧场版分支），示例一旦与实际重命名不符就比没有示例更糟。
const preview = ref<RenamePreview | null>(null);

// 两个模板各自一组示例：剧集三行（整数集 / 半集 12.5 / 字幕），剧场版一行。
// 某个模板校验失败时只隐藏它自己那一组，另一组照常显示。
const episodeExamples = computed(() => {
  const result = preview.value;
  if (!result) return [];
  return [result.episode, result.half_episode, result.subtitle].filter(Boolean);
});

const movieExamples = computed(() => {
  const movie = preview.value?.movie;
  return movie ? [movie] : [];
});

const folderExamples = computed(() => {
  const folder = preview.value?.folder;
  return folder ? [folder] : [];
});

const fetchPreview = useDebounceFn(async () => {
  if (manage.value.rename_method !== 'template') {
    preview.value = null;
    return;
  }
  try {
    preview.value = await apiConfig.previewRenameTemplate({
      template: manage.value.rename_template ?? '',
      movie_template: manage.value.movie_rename_template ?? '',
      folder_template: manage.value.folder_template ?? '',
    });
  } catch {
    // 预览失败不该打断设置流程；不显示示例即可
    preview.value = null;
  }
}, 300);

watch(
  () => [
    manage.value.rename_method,
    manage.value.rename_template,
    manage.value.movie_rename_template,
    manage.value.folder_template,
  ],
  () => fetchPreview(),
  { immediate: true }
);
const revisionConflictPolicies: RevisionConflictPolicy = ['hold', 'replace'];

const revisionConflictOptions = computed<SelectItem[]>(() => [
  {
    id: 1,
    label: t('config.manage_set.revision_conflict_hold'),
    value: revisionConflictPolicies[0],
  },
  {
    id: 2,
    label: t('config.manage_set.revision_conflict_replace'),
    value: revisionConflictPolicies[1],
  },
]);

// 拆成两段：模板区块要紧跟在"重命名方式"下面，而它不是 ab-setting，
// 只能插在两个 v-for 之间。
const itemsBeforeTemplate = computed<SettingItem<BangumiManage>[]>(() => [
  {
    configKey: 'enable',
    label: () => t('config.manage_set.enable'),
    type: 'switch',
  },
  {
    configKey: 'rename_method',
    label: () => t('config.manage_set.method'),
    type: 'select',
    prop: {
      items: renameMethod,
    },
  },
]);

const itemsAfterTemplate = computed<SettingItem<BangumiManage>[]>(() => [
  {
    configKey: 'revision_conflict_policy',
    label: () => t('config.manage_set.revision_conflict_policy'),
    description: t('config.manage_set.revision_conflict_hint'),
    type: 'select',
    prop: {
      items: revisionConflictOptions.value,
    },
    bottomLine: true,
  },
  {
    configKey: 'eps_complete',
    label: () => t('config.manage_set.eps'),
    type: 'switch',
  },
  {
    configKey: 'group_tag',
    label: () => t('config.manage_set.group_tag'),
    type: 'switch',
  },
  {
    configKey: 'remove_bad_torrent',
    label: () => t('config.manage_set.delete_bad_torrent'),
    type: 'switch',
  },
  {
    configKey: 'track_orphans',
    label: () => t('config.manage_set.track_orphans'),
    type: 'switch',
  },
]);
</script>

<template>
  <ab-fold-panel :title="$t('config.manage_set.title')">
    <div space-y-8>
      <ab-setting
        v-for="i in itemsBeforeTemplate"
        :key="i.configKey"
        v-bind="i"
        v-model:data="manage[i.configKey]"
      ></ab-setting>

      <!-- 模板区块紧跟"重命名方式"，不走 items 循环：ab-setting 没有 slot，
           而示例块必须是 ab-field 的兄弟节点——放进 ab-field 的默认 slot 会
           被挤进桌面端那列 200px 宽的控件区里。同 config-search-provider.vue。 -->
      <div v-if="manage.rename_method === 'template'" space-y-8>
        <ab-field
          :label="$t('config.manage_set.rename_template_folder')"
          :error="preview?.folder_error ?? ''"
        >
          <ab-input
            v-model="manage.folder_template"
            type="text"
            :error="Boolean(preview?.folder_error)"
            :placeholder="folderPlaceholder"
          />
        </ab-field>

        <div v-if="folderExamples.length" class="template-preview">
          <div class="template-preview-label">
            {{ $t('config.manage_set.rename_template_example') }}
          </div>
          <div
            v-for="line in folderExamples"
            :key="line"
            class="template-preview-line"
          >
            {{ line }}
          </div>
        </div>

        <div class="hint-text">
          <div class="template-variables">
            {{ $t('config.manage_set.rename_template_variables') }}:
            <span class="template-variables-list">{{
              folderVariableList
            }}</span>
          </div>
        </div>

        <ab-field
          :label="$t('config.manage_set.rename_template')"
          :error="preview?.error ?? ''"
        >
          <ab-input
            v-model="manage.rename_template"
            type="text"
            :error="Boolean(preview?.error)"
            :placeholder="episodePlaceholder"
          />
        </ab-field>

        <div v-if="episodeExamples.length" class="template-preview">
          <div class="template-preview-label">
            {{ $t('config.manage_set.rename_template_example') }}
          </div>
          <div
            v-for="line in episodeExamples"
            :key="line"
            class="template-preview-line"
          >
            {{ line }}
          </div>
        </div>

        <ab-field
          :label="$t('config.manage_set.rename_template_movie')"
          :error="preview?.movie_error ?? ''"
        >
          <ab-input
            v-model="manage.movie_rename_template"
            type="text"
            :error="Boolean(preview?.movie_error)"
            :placeholder="moviePlaceholder"
          />
        </ab-field>

        <div v-if="movieExamples.length" class="template-preview">
          <div class="template-preview-label">
            {{ $t('config.manage_set.rename_template_example') }}
          </div>
          <div
            v-for="line in movieExamples"
            :key="line"
            class="template-preview-line"
          >
            {{ line }}
          </div>
        </div>

        <div class="hint-text">
          <div class="template-variables">
            {{ $t('config.manage_set.rename_template_variables') }}:
            <span class="template-variables-list">{{ variableList }}</span>
          </div>
          {{ $t('config.manage_set.rename_template_hint') }}
        </div>
      </div>

      <ab-setting
        v-for="i in itemsAfterTemplate"
        :key="i.configKey"
        v-bind="i"
        v-model:data="manage[i.configKey]"
      ></ab-setting>
    </div>
  </ab-fold-panel>
</template>

<style lang="scss" scoped>
.hint-text {
  font-size: 12px;
  color: var(--color-text-secondary);
  line-height: 1.5;
}

.template-variables {
  margin-bottom: 4px;
}

.template-variables-list {
  font-family: var(--font-mono);
  color: var(--color-text);
  word-break: break-all;
}

.template-preview {
  padding: 8px 12px;
  background: var(--color-surface-2);
  border-radius: var(--radius-sm);
}

.template-preview-label {
  font-size: 12px;
  color: var(--color-text-muted);
  margin-bottom: 4px;
}

.template-preview-line {
  font-size: 12px;
  color: var(--color-text-secondary);
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  word-break: break-all;
  line-height: 1.6;
}
</style>
