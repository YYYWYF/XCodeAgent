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
  sequence: number
): AgentTrialTurn {
  const normalizedSequence = Math.max(1, Math.trunc(sequence))
  const userMessage = prompt.trim()
  const apiDependency = agent.apiDependencies[0] || '无 API 调用'
  const endpoint =
    apiDependency === 'GET /api/rechecks/my' ? `${apiDependency}?status=待审核` : apiDependency
  const isFollowUp = /还有|注意|补充|然后|接着/.test(userMessage)
  return {
    sequence: normalizedSequence,
    userMessage,
    assistantMessage: isFollowUp
      ? '还需要注意两点：一是确认每条回检单的整改附件可以正常打开，二是保留与审核人的沟通记录。涉及提交或修改的操作仍需要你本人确认。'
      : '你当前有 2 条待审核回检单。建议先核对整改说明和附件是否完整，再联系对应审核人确认处理时限；我不会代替你提交或修改回检单。',
    toolName: agent.tools[0] || '未调用工具',
    endpoint,
    evidence: '仅返回当前用户可见数据 · 2 条'
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

/** 生成供用户确认的智能体 Markdown 详细设计文档。 */
export function buildAgentDesignDoc(agent: DevelopmentPlanningAgent): string {
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
    `- ${agent.model}（模型标识：\`${agent.modelId}\`）`,
    '',
    '## 对话体验',
    '',
    '- 支持连续多轮消息，发送后立即显示用户消息和智能体生成状态。',
    '- 回复完成后保留本次试运行上下文，可继续追问或清空会话。',
    '- 工具调用过程作为可展开信息展示，不打断主要对话。',
    '',
    '## 记忆',
    '',
    '- 会话内保留必要的最近对话和工具结果摘要。',
    '- 不跨用户共享业务数据，不依赖无界历史；长内容保存为稳定引用。',
    '',
    '## 工具',
    '',
    ...markdownList(agent.tools, '暂无工具'),
    ...apiReferenceMarkdown(agent),
    '',
    '## 知识检索',
    '',
    ...markdownList(agent.knowledgeReferences, '暂无已确认知识引用'),
    '- 优先检索当前应用已确认的需求、项目计划和业务知识。',
    '- 检索不到可靠内容时明确说明，不使用未经确认的信息补全答案。',
    ''
  ].join('\n')
}

/** 生成与右侧源码预览配套的智能体定义示例。 */
export function buildAgentSource(agent: DevelopmentPlanningAgent): AgentSourceArtifact {
  const className = `${toJavaClassName(agent.id)}Agent`
  const tools = javaList(agent.tools)
  const apiDependencies = javaList(agent.apiDependencies)
  const apiReferences = javaList(
    agent.apiReferences.map(
      (reference) =>
        `${reference.apiContractId}:${reference.endpointId} ${reference.method.toUpperCase()} ${reference.path}`
    )
  )
  const entityIds = javaList(agent.entityIds)
  const knowledgeReferences = javaList(agent.knowledgeReferences)
  const permissions = javaList(agent.permissions)
  const content = [
    'package com.xcodeagent.generated.agent;',
    '',
    'import com.xcodeagent.runtime.agent.AgentDefinition;',
    'import com.xcodeagent.runtime.agent.AgentRequest;',
    'import com.xcodeagent.runtime.agent.AgentResponse;',
    'import com.xcodeagent.runtime.agent.AgentRuntime;',
    'import com.xcodeagent.runtime.agent.ApplicationAgent;',
    'import java.util.List;',
    'import org.springframework.stereotype.Component;',
    '',
    '/** 创建页面可消费的业务智能体，并由运行时统一执行模型、权限与工具策略。 */',
    '@Component',
    `public final class ${className} {`,
    '  private final ApplicationAgent delegate;',
    '',
    `  /** 初始化 ${agent.label} 的定义与运行时代理。 */`,
    `  public ${className}(AgentRuntime runtime) {`,
    '    AgentDefinition definition = AgentDefinition.builder()',
    `        .id("${escapeJavaString(agent.id)}")`,
    `        .name("${escapeJavaString(agent.label)}")`,
    `        .model("${escapeJavaString(agent.model)}")`,
    `        .modelId("${escapeJavaString(agent.modelId)}")`,
    `        .purpose("${escapeJavaString(agent.purpose)}")`,
    `        .tools(${tools})`,
    `        .apiDependencies(${apiDependencies})`,
    `        .apiReferences(${apiReferences})`,
    `        .entityIds(${entityIds})`,
    `        .knowledgeReferences(${knowledgeReferences})`,
    `        .permissions(${permissions})`,
    '        .build();',
    '    this.delegate = runtime.create(definition);',
    '  }',
    '',
    '  /** 处理一轮用户消息，并返回包含工具证据的智能体回复。 */',
    '  public AgentResponse chat(AgentRequest request) {',
    '    return delegate.chat(request);',
    '  }',
    '}',
    ''
  ].join('\n')
  return {
    filePath: `backend/src/main/java/com/xcodeagent/generated/agent/${className}.java`,
    content
  }
}

/** 生成智能体受控工具适配器的 Java 代码，确保工具绑定与智能体定义分离。 */
export function buildAgentToolAdapterSource(agent: DevelopmentPlanningAgent): AgentSourceArtifact {
  const className = `${toJavaClassName(agent.id)}ToolAdapter`
  const references = agent.apiReferences.length > 0 ? agent.apiReferences : []
  const referenceComments = references.length > 0
    ? references.map(
        (reference) =>
          ` * - ${reference.endpointId}: ${reference.method.toUpperCase()} ${reference.path}；实体：${reference.entityIds.join('、') || '无'}`
      )
    : [' * - 当前智能体没有声明 API 工具。']
  const content = [
    'package com.xcodeagent.generated.tool;',
    '',
    'import com.xcodeagent.runtime.agent.AgentToolResult;',
    'import com.xcodeagent.runtime.agent.ToolContext;',
    'import org.springframework.stereotype.Component;',
    '',
    '/** 智能体工具适配器只暴露已确认的 API 与实体范围，不允许越权扩展。 */',
    ...referenceComments,
    '@Component',
    `public final class ${className} {`,
    '',
    '  /** 使用当前用户上下文执行受控查询，并把来源和失败信息返回给运行时。 */',
    '  public AgentToolResult query(ToolContext context) {',
    `    return context.query("${escapeJavaString(references[0]?.endpointId || 'none')}")`,
    '        .withEvidence(true);',
    '  }',
    '}',
    ''
  ].join('\n')
  return {
    filePath: `backend/src/main/java/com/xcodeagent/generated/tool/${className}.java`,
    content
  }
}

/** 生成页面侧的智能体入口组件，页面只消费稳定的 Agent 会话接口。 */
export function buildAgentPageIntegrationSource(
  agent: DevelopmentPlanningAgent
): AgentSourceArtifact {
  const componentName = toJavaClassName(agent.id)
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

/** 把智能体标识转换为可用的 Java 类名。 */
function toJavaClassName(agentId: string): string {
  const words = agentId
    .trim()
    .split(/[^A-Za-z0-9]+/)
    .filter(Boolean)
  const pascal = words.map((word) => word[0]?.toUpperCase() + word.slice(1)).join('')
  return pascal || 'Application'
}

/** 把文本列表序列化为 Java List.of 表达式。 */
function javaList(items: readonly string[]): string {
  if (items.length === 0) return 'List.of()'
  return `List.of(${items.map((item) => `"${escapeJavaString(item)}"`).join(', ')})`
}

/** 转义 Java 字符串中的反斜线、双引号和换行。 */
function escapeJavaString(value: string): string {
  return value.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\r?\n/g, '\\n')
}
