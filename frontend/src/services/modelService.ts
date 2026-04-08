/**
 * 模型服务 - 与后端模型管理API交互
 */

const API_BASE = '';

export interface ModelInfo {
  model_id: string;
  name: string;
  provider: string;
  max_tokens: number;
  temperature: number;
  supports_streaming: boolean;
  supports_tools: boolean;
  description: string;
  icon: string;
  enabled: boolean;
}

export interface ModelsResponse {
  models: ModelInfo[];
  default_model: string;
  count: number;
}

/**
 * 获取所有可用模型
 */
export async function fetchModels(): Promise<ModelsResponse> {
  const response = await fetch(`${API_BASE}/api/models`);
  if (!response.ok) {
    throw new Error('Failed to fetch models');
  }
  return response.json();
}

/**
 * 获取特定模型详情
 */
export async function fetchModel(modelId: string): Promise<ModelInfo> {
  const response = await fetch(`${API_BASE}/api/models/${modelId}`);
  if (!response.ok) {
    throw new Error('Failed to fetch model');
  }
  return response.json();
}

/**
 * 获取默认模型
 */
export async function fetchDefaultModel(): Promise<{ model_id: string; model: ModelInfo }> {
  const response = await fetch(`${API_BASE}/api/models/default`);
  if (!response.ok) {
    throw new Error('Failed to fetch default model');
  }
  return response.json();
}

/**
 * 设置默认模型
 */
export async function selectModel(modelId: string): Promise<{ success: boolean; message: string }> {
  const response = await fetch(`${API_BASE}/api/models/${modelId}/select`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
  });
  if (!response.ok) {
    throw new Error('Failed to select model');
  }
  return response.json();
}
