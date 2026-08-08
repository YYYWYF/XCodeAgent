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
    placeholder: '描述你想微调的页面或 API，例如修改文案、样式或接口逻辑…',
    label: 'Workflow 输出'
  },
  backend: {
    title: '应用开发助手',
    description: '通过 Workflow 统一推进接口、数据模型、服务逻辑和验证。',
    placeholder: '描述你想微调的页面或 API，例如修改文案、样式或接口逻辑…',
    label: 'Workflow 输出'
  }
}
