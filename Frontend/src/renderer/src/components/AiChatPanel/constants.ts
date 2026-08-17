import type { ChatCopy } from './types'

// 左右分栏按比例分配，适配不同屏幕尺寸：左侧对话区默认占 2/3。
export const DEFAULT_ASSISTANT_PANEL_RATIO = 2 / 3
export const MIN_ASSISTANT_PANEL_RATIO = 0.3
export const MIN_RIGHT_PANEL_RATIO = 0.2
// diff 面板首次打开时按固定目标宽度（px）初始化，内容宽度需求稳定。
export const DEFAULT_DIFF_PANEL_WIDTH = 500
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
