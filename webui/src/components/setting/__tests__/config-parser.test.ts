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
    weekday_source: 'bgm',
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

function mountParser() {
  return mount(ConfigParser, {
    global: {
      stubs: {
        'ab-fold-panel': { template: '<section><slot /></section>' },
        'ab-setting': AbSettingStub,
      },
    },
  });
}

function findSetting(
  wrapper: ReturnType<typeof mountParser>,
  labelKey: string
) {
  const setting = wrapper.findAllComponents(AbSettingStub).find((item) => {
    const label = item.props('label') as () => string;
    return label() === labelKey;
  });
  if (!setting) throw new Error(`setting not found: ${labelKey}`);
  return setting;
}

describe('config-parser', () => {
  it('shows both parser engines and defaults to Classic', async () => {
    const wrapper = mountParser();
    const engine = findSetting(wrapper, 'config.parser_set.engine');

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

  it('shows both air-weekday sources and defaults to bgm.tv', async () => {
    const wrapper = mountParser();
    const source = findSetting(wrapper, 'config.parser_set.weekday_source');

    expect(source.props('data')).toBe('bgm');
    expect(source.props('description')).toBe(
      'config.parser_set.weekday_source_hint'
    );
    expect(source.props('prop')?.items).toEqual([
      {
        id: 1,
        label: 'config.parser_set.weekday_source_bgm',
        value: 'bgm',
      },
      {
        id: 2,
        label: 'config.parser_set.weekday_source_parser',
        value: 'parser',
      },
    ]);

    await source.vm.$emit('update:data', 'parser');
    await nextTick();
    const store = (await import('@/store/config')) as unknown as {
      __parserState: { weekday_source: string };
    };
    expect(store.__parserState.weekday_source).toBe('parser');
  });

  it('offers the four default preferences, all unset by default', async () => {
    const wrapper = mountParser();

    for (const key of [
      'config.parser_set.default_preferred_group',
      'config.parser_set.default_preferred_resolution',
      'config.parser_set.default_preferred_subtitle_language',
      'config.parser_set.default_preferred_subtitle_style',
    ]) {
      // 默认留空＝不设默认偏好，保持下载全部版本的既有行为
      expect(findSetting(wrapper, key).props('data')).toBe('');
    }

    // 两个字幕维度是规范化枚举，第一项为「不限」（空串）
    const language = findSetting(
      wrapper,
      'config.parser_set.default_preferred_subtitle_language'
    );
    expect(language.props('prop')?.items).toEqual([
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

    const style = findSetting(
      wrapper,
      'config.parser_set.default_preferred_subtitle_style'
    );
    expect(style.props('prop')?.items).toEqual([
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
    const wrapper = mountParser();
    const language = findSetting(
      wrapper,
      'config.parser_set.default_preferred_subtitle_language'
    );

    await language.vm.$emit('update:data', 'chs');
    await nextTick();
    const store = (await import('@/store/config')) as unknown as {
      __parserState: { default_preferred_subtitle_language: string };
    };
    expect(store.__parserState.default_preferred_subtitle_language).toBe('chs');
  });
});
