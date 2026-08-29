import { mount } from '@vue/test-utils';
import { defineComponent, nextTick } from 'vue';
import ConfigManage from '../config-manage.vue';

vi.mock('@/hooks/useMyI18n', () => ({
  useMyI18n: () => ({ t: (key: string) => key }),
}));

// 组件通过自动导入拿到 apiConfig，而 api/config → utils/axios → hooks/useAuth
// → router 会在测试环境里初始化 vue-router。预览接口本身在这些用例里也不该
// 发请求，直接把整个 api 模块换掉。
// 工厂会被提升到文件顶部，所以 mock 只能内联定义（本仓库的 vitest 0.30
// 还没有 vi.hoisted）。测试用例通过 import 的 apiConfig 拿到同一个 vi.fn。
vi.mock('@/api/config', () => ({
  apiConfig: {
    previewRenameTemplate: vi.fn().mockResolvedValue({
      episode: '刀剑神域 (2012)/Season 2/[ANi] 刀剑神域 S02E05.mkv',
      half_episode: '刀剑神域 (2012)/Season 2/[ANi] 刀剑神域 S02E12.5.mkv',
      subtitle: '刀剑神域 (2012)/Season 2/[ANi] 刀剑神域 S02E05.zh-tw.ass',
      movie: '游戏人生 零 (2017)/[VCB-Studio] 游戏人生 零 (2017).mkv',
      folder: '刀剑神域 (2012)/',
      error: '',
      movie_error: '',
      folder_error: '',
    }),
  },
}));

vi.mock('@/store/config', async () => {
  const { computed } = await vi.importActual<typeof import('vue')>('vue');
  const manageState = {
    enable: true,
    eps_complete: false,
    rename_method: 'pn',
    rename_template:
      '[{{group}}] {{official_title}} S{{season:2}}E{{episode:2}}',
    movie_rename_template: '[{{group}}] {{official_title}} ({{year}})',
    folder_template: '{{official_title}} ({{year}})',
    revision_conflict_policy: 'hold',
    group_tag: false,
    remove_bad_torrent: false,
    track_orphans: true,
  };
  return {
    __manageState: manageState,
    useConfigStore: () => ({
      getSettingGroup: () => computed(() => manageState),
    }),
  };
});

const AbSettingStub = defineComponent({
  name: 'AbSettingStub',
  props: {
    data: { type: [String, Boolean], default: undefined },
    description: { type: String, default: '' },
    label: { type: [String, Function], required: true },
    prop: { type: Object, default: undefined },
    type: { type: String, required: true },
  },
  emits: ['update:data'],
  template: '<div class="setting-stub"></div>',
});

// Vue 编译器会把 resolveComponent 提升到 render 函数顶部，所以即使模板区块
// 被 v-if 关掉，ab-field / ab-input 也必须有 stub，否则每次挂载都刷告警。
const stubs = {
  'ab-fold-panel': { template: '<section><slot /></section>' },
  'ab-setting': AbSettingStub,
  'ab-field': { props: ['label', 'error'], template: '<div><slot /></div>' },
  'ab-input': { template: '<input />' },
};

function mountManage() {
  return mount(ConfigManage, { global: { stubs } });
}

async function manageState() {
  const store = (await import('@/store/config')) as unknown as {
    __manageState: Record<string, unknown>;
  };
  return store.__manageState;
}

describe('config-manage', () => {
  it('offers a safe hold default and an explicit higher-revision replacement', async () => {
    const wrapper = mountManage();
    const settings = wrapper.findAllComponents(AbSettingStub);
    const policy = settings.find((setting) => {
      const label = setting.props('label') as () => string;
      return label() === 'config.manage_set.revision_conflict_policy';
    });

    expect(policy).toBeDefined();
    if (!policy) throw new Error('revision conflict policy setting not found');
    expect(policy.props('data')).toBe('hold');
    expect(policy.props('description')).toBe(
      'config.manage_set.revision_conflict_hint'
    );
    expect(policy.props('prop')?.items).toEqual([
      {
        id: 1,
        label: 'config.manage_set.revision_conflict_hold',
        value: 'hold',
      },
      {
        id: 2,
        label: 'config.manage_set.revision_conflict_replace',
        value: 'replace',
      },
    ]);

    await policy.vm.$emit('update:data', 'replace');
    await nextTick();
    expect((await manageState()).revision_conflict_policy).toBe('replace');
  });

  it('offers template as a rename method', async () => {
    const wrapper = mountManage();
    const method = wrapper
      .findAllComponents(AbSettingStub)
      .find(
        (setting) =>
          (setting.props('label') as () => string)() ===
          'config.manage_set.method'
      );

    expect(method?.props('prop')?.items).toContain('template');
  });

  it('places the template block right below the rename method', async () => {
    const state = await manageState();
    const previous = state.rename_method;
    state.rename_method = 'template';
    try {
      const wrapper = mountManage();
      const children = Array.from(
        wrapper.find('section > div').element.children
      );
      // 启用 → 重命名方式 → 模板区块 → 其余设置项
      expect(children[0].className).toContain('setting-stub');
      expect(children[1].className).toContain('setting-stub');
      expect(children[2].querySelector('.template-variables')).not.toBeNull();
      expect(children[3].className).toContain('setting-stub');
    } finally {
      state.rename_method = previous;
    }
  });

  it('hides the template fields unless the template method is selected', () => {
    const wrapper = mountManage();
    expect(wrapper.find('.template-preview').exists()).toBe(false);
    expect(wrapper.findAll('input')).toHaveLength(0);
  });

  it('renders the rendered examples returned by the backend', async () => {
    const state = await manageState();
    const previous = state.rename_method;
    state.rename_method = 'template';
    try {
      const wrapper = mountManage();
      // 防抖 300ms + 一次 await 让 mock 的 promise 落地
      await new Promise((resolve) => setTimeout(resolve, 350));
      await nextTick();

      // 三个模板各自一个示例框：剧集三行、剧场版一行、文件夹一行。
      // 剧集/剧场版给的是完整路径——文件夹模板会影响它们，示例要跟着变。
      const boxes = wrapper.findAll('.template-preview');
      expect(boxes).toHaveLength(3);
      // 顺序：文件夹 → 剧集 → 剧场版
      expect(
        boxes[0].findAll('.template-preview-line').map((line) => line.text())
      ).toEqual(['刀剑神域 (2012)/']);
      expect(
        boxes[1].findAll('.template-preview-line').map((line) => line.text())
      ).toEqual([
        '刀剑神域 (2012)/Season 2/[ANi] 刀剑神域 S02E05.mkv',
        '刀剑神域 (2012)/Season 2/[ANi] 刀剑神域 S02E12.5.mkv',
        '刀剑神域 (2012)/Season 2/[ANi] 刀剑神域 S02E05.zh-tw.ass',
      ]);
      expect(
        boxes[2].findAll('.template-preview-line').map((line) => line.text())
      ).toEqual(['游戏人生 零 (2017)/[VCB-Studio] 游戏人生 零 (2017).mkv']);
      // "只影响新下载"的提示必须始终可见。第一个 .hint-text 是文件夹变量
      // 说明（紧跟文件夹模板），提示文案在最后那一段里。
      const hints = wrapper.findAll('.hint-text');
      expect(hints[hints.length - 1].text()).toContain(
        'config.manage_set.rename_template_hint'
      );
      // 变量名是代码字面量，必须由组件渲染而不是走 i18n——放进 i18n JSON 会
      // 被 vue-i18n 当成嵌套占位符，整个构建会失败（error code 9）。
      const lists = wrapper.findAll('.template-variables-list');
      // 文件夹变量说明紧跟文件夹模板，排在最前；文件夹在添加种子时生成，
      // 拿不到 parser_title / episode / folder_name
      expect(lists[0].text()).toBe(
        '{{official_title}} {{year}} {{group}} {{resolution}} ' +
          '{{source}} {{subtitle}}'
      );
      expect(lists[1].text()).toBe(
        '{{parser_title}} {{official_title}} {{folder_name}} {{season:2}} ' +
          '{{episode:2}} {{group}} {{year}} {{resolution}} {{source}} ' +
          '{{subtitle}}'
      );
    } finally {
      state.rename_method = previous;
    }
  });
});
