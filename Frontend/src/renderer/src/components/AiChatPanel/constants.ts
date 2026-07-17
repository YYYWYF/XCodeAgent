import type { ChatCopy } from './types'

export const DEFAULT_ASSISTANT_PANEL_WIDTH = 660
export const DEFAULT_DIFF_PANEL_WIDTH = 500
export const MIN_ASSISTANT_PANEL_WIDTH = 520
export const MIN_RIGHT_PANEL_WIDTH = 380
export const SPLIT_HANDLE_WIDTH = 10

export const chatCopy: ChatCopy = {
  frontend: {
    title: '应用开发助手',
    description: '通过 Workflow 统一推进需求判断、计划、构建、验证和交付。',
    empty: '暂无 Workflow 输出',
    placeholder: '输入你想开发或修改的应用需求...',
    label: 'Workflow 输出'
  },
  backend: {
    title: '应用开发助手',
    description: '通过 Workflow 统一推进接口、数据模型、服务逻辑和验证。',
    empty: '暂无 Workflow 输出',
    placeholder: '输入接口、服务或应用开发需求...',
    label: 'Workflow 输出'
  }
}
