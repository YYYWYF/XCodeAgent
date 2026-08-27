export const DEFAULT_AGENT_CONFIG_MODEL = 'minimax-m2p5-229b-w8a8'

export type AgentConfigResourceKind = 'skills' | 'knowledge' | 'tools'

export type AgentConfigResource = {
  id: string
  name: string
  description: string
}

export type AgentConfigModelSettings = {
  model: string
  deepThinking: boolean
  temperature: number
  topP: number
  frequencyPenalty: number
  presencePenalty: number
  maxTokens: number
  otherParameters: string[]
}

export type AgentConfigConversationSettings = {
  multiTurn: boolean
  toolEvidence: boolean
  retryOnFailure: boolean
}

export type AgentConfigState = {
  model: AgentConfigModelSettings
  skills: AgentConfigResource[]
  knowledge: AgentConfigResource[]
  tools: AgentConfigResource[]
  conversation: AgentConfigConversationSettings
}

export type AgentConfigRevisionStatus =
  | 'clean'
  | 'draft'
  | 'pending_generation'
  | 'generating'
  | 'awaiting_acceptance'
  | 'error'

export type AgentConfigSessionState = {
  activeConfig: AgentConfigState
  draftConfig: AgentConfigState
  pendingConfig?: AgentConfigState
  hasAppliedRevision: boolean
  status: AgentConfigRevisionStatus
  error?: string
}

/** 判断当前版本中的智能体配置是否可编辑；未发布版本允许从后续阶段发起增量修订。 */
export function isAgentConfigEditable(input: {
  agentId?: string
  versionReadOnly: boolean
}): boolean {
  return Boolean(input.agentId?.trim()) && !input.versionReadOnly
}

/** 创建配置面板的默认配置，确保每个智能体会话拥有独立的可编辑副本。 */
export function createInitialAgentConfig(): AgentConfigState {
  return {
    model: {
      model: DEFAULT_AGENT_CONFIG_MODEL,
      deepThinking: false,
      temperature: 0.7,
      topP: 0.5,
      frequencyPenalty: 0,
      presencePenalty: 0,
      maxTokens: 4000,
      otherParameters: []
    },
    skills: [],
    knowledge: [],
    tools: [],
    conversation: {
      multiTurn: true,
      toolEvidence: true,
      retryOnFailure: true
    }
  }
}

/** 深拷贝配置对象，避免面板草稿修改意外改变当前生效配置。 */
export function cloneAgentConfig(config: AgentConfigState): AgentConfigState {
  return {
    model: {
      ...config.model,
      otherParameters: [...config.model.otherParameters]
    },
    skills: config.skills.map((resource) => ({ ...resource })),
    knowledge: config.knowledge.map((resource) => ({ ...resource })),
    tools: config.tools.map((resource) => ({ ...resource })),
    conversation: { ...config.conversation }
  }
}

/** 生成稳定配置指纹，用于判断草稿是否相对生效配置发生了真实变化。 */
export function agentConfigFingerprint(config: AgentConfigState): string {
  const resources = (items: AgentConfigResource[]): string[] =>
    items.map((resource) => resource.id).sort()
  return JSON.stringify({
    model: config.model,
    skills: resources(config.skills),
    knowledge: resources(config.knowledge),
    tools: resources(config.tools),
    conversation: config.conversation
  })
}

/** 判断两份智能体配置是否等价，忽略资源在列表中的展示顺序。 */
export function areAgentConfigsEqual(left: AgentConfigState, right: AgentConfigState): boolean {
  return agentConfigFingerprint(left) === agentConfigFingerprint(right)
}

/** 创建一个初始的会话级配置状态，初始草稿与生效版本彼此隔离。 */
export function createAgentConfigSessionState(): AgentConfigSessionState {
  const activeConfig = createInitialAgentConfig()
  return {
    activeConfig,
    draftConfig: cloneAgentConfig(activeConfig),
    hasAppliedRevision: false,
    status: 'clean'
  }
}

/** 汇总配置中发生变化的模块，供确认卡和对话进度说明复用。 */
export function changedAgentConfigSections(
  activeConfig: AgentConfigState,
  draftConfig: AgentConfigState
): Array<{ key: 'model' | AgentConfigResourceKind | 'conversation'; label: string }> {
  const sections: Array<{
    key: 'model' | AgentConfigResourceKind | 'conversation'
    label: string
  }> = []
  if (JSON.stringify(activeConfig.model) !== JSON.stringify(draftConfig.model)) {
    sections.push({ key: 'model', label: '模型' })
  }
  if (
    agentConfigFingerprintForResources(activeConfig.skills) !==
    agentConfigFingerprintForResources(draftConfig.skills)
  ) {
    sections.push({ key: 'skills', label: '技能' })
  }
  if (
    agentConfigFingerprintForResources(activeConfig.knowledge) !==
    agentConfigFingerprintForResources(draftConfig.knowledge)
  ) {
    sections.push({ key: 'knowledge', label: '知识检索' })
  }
  if (
    agentConfigFingerprintForResources(activeConfig.tools) !==
    agentConfigFingerprintForResources(draftConfig.tools)
  ) {
    sections.push({ key: 'tools', label: '工具' })
  }
  if (JSON.stringify(activeConfig.conversation) !== JSON.stringify(draftConfig.conversation)) {
    sections.push({ key: 'conversation', label: '对话体验' })
  }
  return sections
}

/** 生成资源模块的稳定指纹，避免比较描述文案或显示顺序造成误判。 */
function agentConfigFingerprintForResources(resources: AgentConfigResource[]): string {
  return JSON.stringify(resources.map((resource) => resource.id).sort())
}

/** 校验 AG-UI 恢复快照中的候选配置，避免不完整的外部数据进入代码生成器。 */
export function isAgentConfigState(value: unknown): value is AgentConfigState {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<AgentConfigState>
  return Boolean(
    candidate.model &&
      typeof candidate.model === 'object' &&
      Array.isArray(candidate.skills) &&
      Array.isArray(candidate.knowledge) &&
      Array.isArray(candidate.tools) &&
      candidate.conversation &&
      typeof candidate.conversation === 'object'
  )
}
