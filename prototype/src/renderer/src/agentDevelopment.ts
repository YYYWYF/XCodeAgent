import type { AgentConfigResource, AgentConfigState } from './agentConfig'

export type AgentApiReference = {
  apiContractId: string
  endpointId: string
  method: string
  path: string
  purpose: string
  entityIds: string[]
}

export type AgentDependencyEntityState = {
  entityId: string
  label: string
  designed?: boolean
  hasDetailPlan?: boolean
  detailPlanStatus?: string
}

export type DevelopmentPlanningAgent = {
  id: string
  label: string
  purpose: string
  model: string
  modelId: string
  apiDependencies: string[]
  apiReferences: AgentApiReference[]
  entityIds: string[]
  pageIds: string[]
  tools: string[]
  permissions: string[]
  acceptanceCriteria: string[]
  knowledgeReferences: string[]
  designed: boolean
  hasDetailPlan: boolean
  detailPlanStatus?: string
}

export type AgentSessionIdentity = {
  artifactIds?: readonly string[]
}

export type AgentSourceArtifact = {
  filePath: string
  content: string
}

export type AgentTrialTurn = {
  sequence: number
  userMessage: string
  assistantMessage: string
  toolName: string
  endpoint: string
  evidence: string
}

export type AgentDetailBlocker = {
  type: 'agent'
  targetType: 'agent'
  targetId: string
  agentId: string
  label: string
  model: string
  modelId: string
  purpose: string
  apiReferences: AgentApiReference[]
  entityIds: string[]
  knowledgeReferences: string[]
}

/** 生成智能体产物的稳定领域标识。 */
export function agentArtifactId(agentId: string): string {
  return `agent:${agentId.trim()}`
}

/** 从统一产物标识中恢复智能体标识，非智能体产物返回空值。 */
export function agentIdFromArtifactId(artifactId: string): string | undefined {
  const normalized = artifactId.trim()
  if (!normalized.startsWith('agent:')) return undefined
  const agentId = normalized.slice('agent:'.length).trim()
  return agentId || undefined
}

/** 判断会话是否显式持有指定智能体产物。 */
export function sessionMatchesAgent(session: AgentSessionIdentity, agentId: string): boolean {
  return (session.artifactIds || []).includes(agentArtifactId(agentId))
}

/** 生成可持久化到智能体会话历史中的详细设计挡板。 */
export function buildAgentDetailBlocker(agent: DevelopmentPlanningAgent): AgentDetailBlocker {
  return {
    type: 'agent',
    targetType: 'agent',
    targetId: agent.id,
    agentId: agent.id,
    label: agent.label,
    model: agent.model,
    modelId: agent.modelId,
    purpose: agent.purpose,
    apiReferences: agent.apiReferences,
    entityIds: agent.entityIds,
    knowledgeReferences: agent.knowledgeReferences
  }
}

/** 返回智能体当前声明但尚未完成详细设计确认的实体依赖。 */
export function missingAgentEntityIds(
  agent: DevelopmentPlanningAgent,
  entities: readonly AgentDependencyEntityState[]
): string[] {
  const requiredEntityIds = new Set([
    ...agent.entityIds,
    ...agent.apiReferences.flatMap((reference) => reference.entityIds)
  ])
  return [...requiredEntityIds].filter((entityId) => {
    const entity = entities.find((candidate) => candidate.entityId === entityId)
    return !entity || !(entity.designed || entity.hasDetailPlan || entity.detailPlanStatus === 'confirmed')
  })
}

/** 生成一次智能体试运行对话轮次，供原型连续追加用户消息与智能体回复。 */
export function createAgentTrialTurn(
  agent: DevelopmentPlanningAgent,
  prompt: string,
  sequence: number,
  config?: AgentConfigState
): AgentTrialTurn {
  const normalizedSequence = Math.max(1, Math.trunc(sequence))
  const userMessage = prompt.trim()
  const apiDependency = agent.apiDependencies[0] || '无 API 调用'
  const endpoint =
    apiDependency === 'GET /api/rechecks/my' ? `${apiDependency}?status=待审核` : apiDependency
  const configuredTools = configuredResourceNames(agent.tools, config?.tools)
  const modelLabel = config?.model.model || agent.model
  const isFollowUp = /还有|注意|补充|然后|接着/.test(userMessage)
  return {
    sequence: normalizedSequence,
    userMessage,
    assistantMessage: isFollowUp
      ? '还需要注意两点：一是确认每条回检单的整改附件可以正常打开，二是保留与审核人的沟通记录。涉及提交或修改的操作仍需要你本人确认。'
      : '你当前有 2 条待审核回检单。建议先核对整改说明和附件是否完整，再联系对应审核人确认处理时限；我不会代替你提交或修改回检单。',
    toolName: configuredTools[0] || '未调用工具',
    endpoint,
    evidence: `${modelLabel} · 仅返回当前用户可见数据 · 2 条`
  }
}

/** 把字符串列表序列化为可读的 Markdown 条目。 */
function markdownList(items: readonly string[], fallback: string): string[] {
  return (items.length > 0 ? items : [fallback]).map((item) => `- ${item}`)
}

/** 把智能体的 API 绑定转换为用户可核对的稳定引用条目。 */
function apiReferenceMarkdown(agent: DevelopmentPlanningAgent): string[] {
  if (agent.apiReferences.length === 0) return ['- 暂无稳定 API 引用']
  return agent.apiReferences.map((reference) => {
    const entities = reference.entityIds.length > 0 ? `；实体：${reference.entityIds.join('、')}` : ''
    return `- ${reference.method.toUpperCase()} ${reference.path}（契约：\`${reference.apiContractId}\`，端点：\`${reference.endpointId}\`）${entities}`
  })
}

/** 合并智能体基线资源与配置面板新增资源，生成稳定且不重复的展示列表。 */
function configuredResourceNames(
  baseResources: readonly string[],
  configuredResources?: readonly AgentConfigResource[]
): string[] {
  const names = [...baseResources, ...(configuredResources || []).map((resource) => resource.name)]
  return [...new Set(names.filter((name) => name.trim()))]
}

/** 将配置中的布尔开关转换为用户文档中的启用或关闭文案。 */
function enabledLabel(value: boolean | undefined): string {
  return value === false ? '关闭' : '启用'
}

/** 将配置面板中的 Markdown 标题降级，嵌入正式设计文档时保持十段主结构不变。 */
function personaReplyLogicForDesign(personaReplyLogic: string): string[] {
  return personaReplyLogic
    .split(/\r?\n/)
    .map((line) => (line.startsWith('## ') ? `#### ${line.slice('## '.length)}` : line))
}

/** 生成供用户确认的智能体 Markdown 详细设计文档。 */
export function buildAgentDesignDoc(
  agent: DevelopmentPlanningAgent,
  config?: AgentConfigState
): string {
  const modelLabel = config?.model.model || agent.model
  const modelId = config?.model.model || agent.modelId
  const tools = configuredResourceNames(agent.tools, config?.tools)
  const knowledgeReferences = configuredResourceNames(agent.knowledgeReferences, config?.knowledge)
  const skills = (config?.skills || []).map((resource) => resource.name)
  const modelSettings = config?.model
  const personaReplyLogic = config?.personaReplyLogic.trim() || ''
  return [
    `# ${agent.label} 智能体设计`,
    '',
    '## 任务',
    '',
    agent.purpose,
    '',
    ...markdownList(
      agent.pageIds.map((pageId) => `服务页面：\`${pageId}\``),
      '暂未绑定页面'
    ),
    ...apiReferenceMarkdown(agent),
    '',
    '## 规则',
    '',
    '- 只回答需求回检相关问题，并明确说明信息来源。',
    '- 先理解当前问题，再按需调用工具，核验证据后生成回复。',
    '- 本设计需要人工确认后才能进入构建；修订后需要重新确认。',
    ...(skills.length > 0 ? [`- 已配置技能：${skills.join('、')}。`] : []),
    ...(personaReplyLogic
      ? ['', '### 人设与回复逻辑', '', ...personaReplyLogicForDesign(personaReplyLogic)]
      : []),
    '',
    '## 限制',
    '',
    ...markdownList(agent.permissions, '遵循应用默认权限'),
    '- 不展示隐藏思维链，不编造工具未返回的业务数据。',
    '',
    '## 输入',
    '',
    '- 用户在试运行或业务页面中发送的自然语言消息。',
    '- 当前页面上下文、当前会话历史及必要的用户身份范围。',
    '',
    '## 输出',
    '',
    '- 面向用户的直接回复。',
    '- 工具调用摘要、数据来源与可核验证据。',
    '- 工具失败时返回错误摘要和重试建议，高风险动作转人工处理。',
    '',
    '## 模型',
    '',
    `- ${modelLabel}（模型标识：\`${modelId}\`）`,
    ...(modelSettings
      ? [
          `- 深度思考：${enabledLabel(modelSettings.deepThinking)}`,
          `- 生成参数：temperature=${modelSettings.temperature}，topP=${modelSettings.topP}，frequencyPenalty=${modelSettings.frequencyPenalty}，presencePenalty=${modelSettings.presencePenalty}。`,
          `- 输出上限：maxTokens=${modelSettings.maxTokens}。`
        ]
      : []),
    '',
    '## 对话体验',
    '',
    '- 支持连续多轮消息，发送后立即显示用户消息和智能体生成状态。',
    '- 回复完成后保留本次试运行上下文，可继续追问或清空会话。',
    '- 工具调用过程作为可展开信息展示，不打断主要对话。',
    ...(config
      ? [
          `- 连续多轮对话：${enabledLabel(config.conversation.multiTurn)}；工具证据：${enabledLabel(config.conversation.toolEvidence)}；失败后允许重试：${enabledLabel(config.conversation.retryOnFailure)}。`
        ]
      : []),
    '',
    '## 记忆',
    '',
    '- 会话内保留必要的最近对话和工具结果摘要。',
    '- 不跨用户共享业务数据，不依赖无界历史；长内容保存为稳定引用。',
    '',
    '## 工具',
    '',
    ...markdownList(tools, '暂无工具'),
    ...apiReferenceMarkdown(agent),
    '',
    '## 知识检索',
    '',
    ...markdownList(knowledgeReferences, '暂无已确认知识引用'),
    '- 优先检索当前应用已确认的需求、项目计划和业务知识。',
    '- 检索不到可靠内容时明确说明，不使用未经确认的信息补全答案。',
    ''
  ].join('\n')
}

/** 生成与右侧源码预览配套的智能体定义示例。 */
export function buildAgentSource(
  agent: DevelopmentPlanningAgent,
  config?: AgentConfigState
): AgentSourceArtifact {
  const className = `${toPythonClassName(agent.id)}Agent`
  const moduleName = toPythonModuleName(agent.id)
  const tools = pythonList(configuredResourceNames(agent.tools, config?.tools))
  const skills = pythonList((config?.skills || []).map((resource) => resource.name))
  const apiDependencies = pythonList(agent.apiDependencies)
  const apiReferences = pythonList(
    agent.apiReferences.map(
      (reference) =>
        `${reference.apiContractId}:${reference.endpointId} ${reference.method.toUpperCase()} ${reference.path}`
    )
  )
  const entityIds = pythonList(agent.entityIds)
  const knowledgeReferences = pythonList(
    configuredResourceNames(agent.knowledgeReferences, config?.knowledge)
  )
  const permissions = pythonList(agent.permissions)
  const model = config?.model.model || agent.model
  const modelId = config?.model.model || agent.modelId
  const modelSettings = config?.model
  const conversation = config?.conversation
  const personaReplyLogic = config?.personaReplyLogic || ''
  const content = [
    'from __future__ import annotations',
    '',
    'from xcodeagent.runtime.agent import (',
    '    AgentDefinition,',
    '    AgentRequest,',
    '    AgentResponse,',
    '    AgentRuntime,',
    '    ApplicationAgent,',
    ')',
    '',
    '# 创建页面可消费的业务智能体，并由运行时统一执行模型、权限与工具策略。',
    `class ${className}:`,
    '    """页面可消费的业务智能体定义。"""',
    '',
    `    # 初始化 ${agent.label} 的定义与运行时代理。`,
    '    def __init__(self, runtime: AgentRuntime) -> None:',
    '        definition = AgentDefinition(',
    `            id=${pythonString(agent.id)},`,
    `            name=${pythonString(agent.label)},`,
    `            model=${pythonString(model)},`,
    `            model_id=${pythonString(modelId)},`,
    `            purpose=${pythonString(agent.purpose)},`,
    `            tools=${tools},`,
    `            skills=${skills},`,
    `            api_dependencies=${apiDependencies},`,
    `            api_references=${apiReferences},`,
    `            entity_ids=${entityIds},`,
    `            knowledge_references=${knowledgeReferences},`,
    `            permissions=${permissions},`,
    ...(config ? [`            persona_reply_logic=${pythonString(personaReplyLogic)},`] : []),
    ...(modelSettings
      ? [
          `            deep_thinking=${pythonBoolean(modelSettings.deepThinking)},`,
          `            temperature=${pythonNumber(modelSettings.temperature)},`,
          `            top_p=${pythonNumber(modelSettings.topP)},`,
          `            frequency_penalty=${pythonNumber(modelSettings.frequencyPenalty)},`,
          `            presence_penalty=${pythonNumber(modelSettings.presencePenalty)},`,
          `            max_tokens=${pythonNumber(modelSettings.maxTokens)},`,
          `            other_parameters=${pythonList(modelSettings.otherParameters)},`
        ]
      : []),
    ...(conversation
      ? [
          `            multi_turn=${pythonBoolean(conversation.multiTurn)},`,
          `            tool_evidence=${pythonBoolean(conversation.toolEvidence)},`,
          `            retry_on_failure=${pythonBoolean(conversation.retryOnFailure)},`
        ]
      : []),
    '        )',
    '        self._delegate: ApplicationAgent = runtime.create(definition)',
    '',
    '    # 处理一轮用户消息，并返回包含工具证据的智能体回复。',
    '    def chat(self, request: AgentRequest) -> AgentResponse:',
    '        return self._delegate.chat(request)',
    ''
  ].join('\n')
  return {
    filePath: `agent-runtime/agents/${moduleName}.py`,
    content
  }
}

/** 生成智能体受控工具适配器的 Python 代码，确保工具绑定与智能体定义分离。 */
export function buildAgentToolAdapterSource(
  agent: DevelopmentPlanningAgent,
  config?: AgentConfigState
): AgentSourceArtifact {
  const className = `${toPythonClassName(agent.id)}ToolAdapter`
  const moduleName = toPythonModuleName(agent.id)
  const references = agent.apiReferences.length > 0 ? agent.apiReferences : []
  const configuredTools = configuredResourceNames(agent.tools, config?.tools)
  const referenceComments =
    references.length > 0
      ? references.map(
          (reference) =>
            `# - ${reference.endpointId}: ${reference.method.toUpperCase()} ${reference.path}；实体：${reference.entityIds.join('、') || '无'}`
        )
      : ['# - 当前智能体没有声明 API 工具。']
  const content = [
    'from xcodeagent.runtime.agent import AgentToolResult, ToolContext',
    '',
    '# 智能体工具适配器只暴露已确认的 API 与实体范围，不允许越权扩展。',
    `# - 已配置工具：${configuredTools.join('、') || '无'}。`,
    ...referenceComments,
    '',
    `class ${className}:`,
    '    """智能体工具适配器，只暴露已确认的 API 与实体范围。"""',
    '',
    '    # 使用当前用户上下文执行受控查询，并把来源和失败信息返回给运行时。',
    '    def query(self, context: ToolContext) -> AgentToolResult:',
    `        return context.query(${pythonString(references[0]?.endpointId || 'none')}).with_evidence(True)`,
    ''
  ].join('\n')
  return {
    filePath: `agent-runtime/tools/${moduleName}_tools.py`,
    content
  }
}

/** 生成页面侧的智能体入口组件，页面只消费稳定的 Agent 会话接口。 */
export function buildAgentPageIntegrationSource(
  agent: DevelopmentPlanningAgent
): AgentSourceArtifact {
  const componentName = toPythonClassName(agent.id)
  const content = [
    "import { useState } from 'react'",
    '',
    '/** 页面内智能体入口只负责会话展示，权限与工具执行由后端 Agent 运行时处理。 */',
    `export function ${componentName}(): JSX.Element {`,
    '  const [open, setOpen] = useState(false)',
    '  return (',
    '    <aside aria-label="智能体助手">',
    `      <button onClick={() => setOpen((current) => !current)} type="button">${agent.label}</button>`,
    '      {open ? <div role="status">可开始连续对话，工具证据和失败状态会在消息中展示。</div> : null}',
    '    </aside>',
    '  )',
    '}',
    ''
  ].join('\n')
  return {
    filePath: `frontend/src/pages/${agent.pageIds[0] || 'application'}/components/${componentName}.tsx`,
    content
  }
}

/** 把智能体标识转换为可用的 Python 类名。 */
function toPythonClassName(agentId: string): string {
  const words = agentId
    .trim()
    .split(/[^A-Za-z0-9]+/)
    .filter(Boolean)
  const pascal = words.map((word) => word[0]?.toUpperCase() + word.slice(1)).join('')
  return pascal || 'Application'
}

/** 把智能体标识转换为符合 Python 约定的模块名。 */
function toPythonModuleName(agentId: string): string {
  const moduleName = agentId
    .trim()
    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
    .replace(/[^A-Za-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .toLowerCase()
  return moduleName || 'application'
}

/** 把文本列表序列化为 Python 列表表达式。 */
function pythonList(items: readonly string[]): string {
  return `[${items.map((item) => pythonString(item)).join(', ')}]`
}

/** 转义为可直接嵌入 Python 源码的字符串字面量。 */
function pythonString(value: string): string {
  return JSON.stringify(value)
    .replace(/\u2028/g, '\\u2028')
    .replace(/\u2029/g, '\\u2029')
}

/** 把配置布尔值转换为 Python 源码中的布尔字面量。 */
function pythonBoolean(value: boolean): string {
  return value ? 'True' : 'False'
}

/** 把有限数值转换为稳定的 Python 数字字面量。 */
function pythonNumber(value: number): string {
  return Number.isFinite(value) ? String(value) : '0'
}
