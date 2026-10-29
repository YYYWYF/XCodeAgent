export const MODULES: Record<string, string> = {
  prompt: '提示词',
  model: '模型',
  memory: '记忆',
  tools: '工具',
  skills: '技能',
  knowledge: '知识',
  context: '上下文'
}
export const FIELDS: Record<string, string> = {
  systemPrompt: '系统提示词',
  persona: '人设',
  role: '角色',
  tone: '表达风格',
  constraints: '业务约束',
  source: '配置来源',
  generation: '生成参数',
  temperature: '随机度',
  enabled: '启用状态',
  bindings: '接口绑定',
  toolId: '工具标识',
  apiContractId: '接口契约',
  endpointId: '接口标识'
}

/** 用普通文本展示模块配置，避免将内部 JSON 当成可编辑的用户产物。 */
export function settingText(value: unknown): string {
  if (value === null || value === undefined) return '未设置'
  if (typeof value === 'boolean') return value ? '启用' : '关闭'
  if (Array.isArray(value)) return value.length ? value.map(settingText).join('；') : '无'
  if (typeof value === 'object')
    return Object.entries(value)
      .map(([key, item]) => `${FIELDS[key] || key}：${settingText(item)}`)
      .join('；')
  return String(value)
}
