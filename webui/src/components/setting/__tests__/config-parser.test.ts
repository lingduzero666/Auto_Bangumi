import { mount } from '@vue/test-utils';
import { defineComponent, nextTick } from 'vue';
import ConfigParser from '../config-parser.vue';

vi.mock('@/hooks/useMyI18n', () => ({
  useMyI18n: () => ({ t: (key: string) => key }),
}));

vi.mock('@/store/config', async () => {
  const { computed } = await vi.importActual<typeof import('vue')>('vue');
  const parserState = {
    enable: true,
    engine: 'classic',
    filter: [] as string[],
    language: 'zh',
    default_preferred_group: '',
    default_preferred_resolution: '',
    default_preferred_subtitle_language: '',
    default_preferred_subtitle_style: '',
  };
  return {
    __parserState: parserState,
    useConfigStore: () => ({
      getSettingGroup: () => computed(() => parserState),
    }),
  };
});

const AbSettingStub = defineComponent({
  name: 'AbSettingStub',
  props: {
    data: { type: [String, Boolean, Array], default: undefined },
    description: { type: String, default: '' },
    label: { type: [String, Function], required: true },
    prop: { type: Object, default: undefined },
    type: { type: String, required: true },
  },
  emits: ['update:data'],
  template: '<div class="setting-stub"></div>',
});

describe('config-parser', () => {
  it('shows both parser engines and defaults to Classic', async () => {
    const wrapper = mount(ConfigParser, {
      global: {
        stubs: {
          'ab-fold-panel': { template: '<section><slot /></section>' },
          'ab-setting': AbSettingStub,
        },
      },
    });
    const settings = wrapper.findAllComponents(AbSettingStub);
    const engine = settings.find((setting) => {
      const label = setting.props('label') as () => string;
      return label() === 'config.parser_set.engine';
    });

    expect(engine).toBeDefined();
    if (!engine) throw new Error('parser engine setting not found');
    expect(engine.props('data')).toBe('classic');
    expect(engine.props('description')).toBe('config.parser_set.engine_hint');
    expect(engine.props('prop')?.items).toEqual([
      {
        id: 1,
        label: 'config.parser_set.engine_classic',
        value: 'classic',
      },
      {
        id: 2,
        label: 'config.parser_set.engine_tokenizer',
        value: 'tokenizer',
      },
    ]);

    await engine.vm.$emit('update:data', 'tokenizer');
    await nextTick();
    const store = (await import('@/store/config')) as unknown as {
      __parserState: { engine: string };
    };
    expect(store.__parserState.engine).toBe('tokenizer');
  });

  it('offers the four default preferences, all unset by default', async () => {
    const wrapper = mount(ConfigParser, {
      global: {
        stubs: {
          'ab-fold-panel': { template: '<section><slot /></section>' },
          'ab-setting': AbSettingStub,
        },
      },
    });
    const settings = wrapper.findAllComponents(AbSettingStub);
    const byLabel = (key: string) =>
      settings.find((setting) => {
        const label = setting.props('label') as () => string;
        return label() === key;
      });

    for (const key of [
      'config.parser_set.default_preferred_group',
      'config.parser_set.default_preferred_resolution',
      'config.parser_set.default_preferred_subtitle_language',
      'config.parser_set.default_preferred_subtitle_style',
    ]) {
      const setting = byLabel(key);
      expect(setting, `${key} setting not found`).toBeDefined();
      // 默认留空＝不设默认偏好，保持下载全部版本的既有行为
      expect(setting?.props('data')).toBe('');
    }

    // 两个字幕维度是规范化枚举，第一项为「不限」（空串）
    const language = byLabel(
      'config.parser_set.default_preferred_subtitle_language'
    );
    expect(language?.props('prop')?.items).toEqual([
      { id: 0, label: 'config.parser_set.preference_unset', value: '' },
      { id: 1, label: 'config.parser_set.subtitle_language_chs', value: 'chs' },
      { id: 2, label: 'config.parser_set.subtitle_language_cht', value: 'cht' },
      {
        id: 3,
        label: 'config.parser_set.subtitle_language_chs_cht',
        value: 'chs_cht',
      },
      { id: 4, label: 'config.parser_set.subtitle_language_jpn', value: 'jpn' },
      { id: 5, label: 'config.parser_set.subtitle_language_eng', value: 'eng' },
    ]);

    const style = byLabel('config.parser_set.default_preferred_subtitle_style');
    expect(style?.props('prop')?.items).toEqual([
      { id: 0, label: 'config.parser_set.preference_unset', value: '' },
      {
        id: 1,
        label: 'config.parser_set.subtitle_style_embedded',
        value: 'embedded',
      },
      {
        id: 2,
        label: 'config.parser_set.subtitle_style_muxed',
        value: 'muxed',
      },
      {
        id: 3,
        label: 'config.parser_set.subtitle_style_external',
        value: 'external',
      },
    ]);
  });

  it('writes a chosen subtitle language back to the config store', async () => {
    const wrapper = mount(ConfigParser, {
      global: {
        stubs: {
          'ab-fold-panel': { template: '<section><slot /></section>' },
          'ab-setting': AbSettingStub,
        },
      },
    });
    const language = wrapper
      .findAllComponents(AbSettingStub)
      .find((setting) => {
        const label = setting.props('label') as () => string;
        return (
          label() === 'config.parser_set.default_preferred_subtitle_language'
        );
      });

    await language?.vm.$emit('update:data', 'chs');
    await nextTick();
    const store = (await import('@/store/config')) as unknown as {
      __parserState: { default_preferred_subtitle_language: string };
    };
    expect(store.__parserState.default_preferred_subtitle_language).toBe('chs');
  });
});
