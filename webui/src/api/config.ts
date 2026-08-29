import type { Config, LLMProviderId, RenamePreview } from '#/config';
import type { ApiSuccess } from '#/api';

export const apiConfig = {
  /**
   * 获取 config 数据
   */
  async getConfig() {
    const { data } = await axios.get<Config>('api/v1/config/get');
    return data;
  },

  /**
   * 更新 config 数据
   * @param newConfig - 需要更新的 config
   */
  async updateConfig(newConfig: Config) {
    const { data } = await axios.patch<ApiSuccess>(
      'api/v1/config/update',
      newConfig
    );
    return data;
  },

  /**
   * 拉取所选 LLM 提供商的可用模型列表
   * （api_key 传掩码时后端回退到已保存的密钥）
   */
  async getLLMModels(payload: {
    provider: LLMProviderId;
    api_key: string;
    base_url: string;
  }) {
    const { data } = await axios.post<{ models: string[] }>(
      'api/v1/config/llm/models',
      payload,
      { silent: true }
    );
    return data.models;
  },

  /**
   * 按模板渲染一组示例文件名。
   * 渲染在后端进行，与真正的重命名走同一套函数，所以示例不会与实际结果不符。
   * 模板非法时 error/movie_error 带回错误文案，HTTP 仍是 200。
   */
  async previewRenameTemplate(payload: {
    template: string;
    movie_template: string;
    folder_template: string;
  }) {
    const { data } = await axios.post<RenamePreview>(
      'api/v1/config/rename/preview',
      payload,
      { silent: true }
    );
    return data;
  },
};
