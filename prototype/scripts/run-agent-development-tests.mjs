import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

const sourceUrl = new URL('../src/renderer/src/agentDevelopment.ts', import.meta.url)
const source = await readFile(sourceUrl, 'utf8')
const transpiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022
  },
  fileName: sourceUrl.pathname
})
const moduleUrl = `data:text/javascript;base64,${Buffer.from(transpiled.outputText).toString('base64')}`
const agentDevelopment = await import(moduleUrl)

const configUrl = new URL('../src/renderer/src/agentConfig.ts', import.meta.url)
const configSource = await readFile(configUrl, 'utf8')
const configTranspiled = ts.transpileModule(configSource, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022
  },
  fileName: configUrl.pathname
})
const configModuleUrl = `data:text/javascript;base64,${Buffer.from(
  configTranspiled.outputText
).toString('base64')}`
const agentConfig = await import(configModuleUrl)

const versionsUrl = new URL('../src/renderer/src/service/applicationVersions.ts', import.meta.url)
const versionsSource = await readFile(versionsUrl, 'utf8')
const versionsTranspiled = ts.transpileModule(versionsSource, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022
  },
  fileName: versionsUrl.pathname
})
const versionsModuleUrl = `data:text/javascript;base64,${Buffer.from(
  versionsTranspiled.outputText
).toString('base64')}`
const applicationVersions = await import(versionsModuleUrl)

assert.equal(
  applicationVersions.isVersionEditable({ status: 'iterating' }),
  true,
  '未生成版本必须保持可编辑'
)
assert.equal(
  applicationVersions.isVersionEditable({ status: 'released' }),
  false,
  '只有已生成版本进入只读态'
)
assert.equal(
  agentConfig.isAgentConfigEditable({ agentId: 'recheck-assistant', versionReadOnly: false }),
  true,
  '当前未发布版本即使处于测试或审查阶段，也必须允许发起智能体配置修订'
)
assert.equal(
  agentConfig.isAgentConfigEditable({ agentId: 'recheck-assistant', versionReadOnly: true }),
  false,
  '已发布或历史版本的智能体配置必须保持只读'
)
assert.equal(
  agentConfig.isAgentConfigEditable({ agentId: '  ', versionReadOnly: false }),
  false,
  '未选中智能体时不能编辑配置'
)

const agent = {
  id: 'recheck-assistant',
  label: '回检填报助手',
  purpose: '解释回检状态并给出下一步建议',
  model: '项目默认模型',
  modelId: 'default-model',
  apiDependencies: ['GET /api/rechecks/my'],
  apiReferences: [
    {
      apiContractId: 'rechecks',
      endpointId: 'ep-my-rechecks',
      method: 'GET',
      path: '/api/rechecks/my',
      purpose: '查询当前用户的回检单',
      entityIds: ['recheck-record']
    }
  ],
  entityIds: ['recheck-record'],
  pageIds: ['my-rechecks'],
  tools: ['查询我的回检单'],
  knowledgeReferences: ['knowledge:recheck-policy'],
  permissions: ['只读当前用户可见数据'],
  acceptanceCriteria: ['回答包含数据来源说明'],
  designed: false,
  hasDetailPlan: false
}

assert.equal(agentDevelopment.agentArtifactId(' recheck-assistant '), 'agent:recheck-assistant')
assert.equal(agentDevelopment.agentIdFromArtifactId('agent:recheck-assistant'), 'recheck-assistant')
assert.equal(agentDevelopment.agentIdFromArtifactId('endpoint:rechecks:list'), undefined)
assert.equal(
  agentDevelopment.sessionMatchesAgent(
    { artifactIds: ['page:my-rechecks', 'agent:recheck-assistant'] },
    'recheck-assistant'
  ),
  true
)
assert.equal(
  agentDevelopment.sessionMatchesAgent({ artifactIds: ['page:my-rechecks'] }, 'recheck-assistant'),
  false
)

const detailBlocker = agentDevelopment.buildAgentDetailBlocker(agent)
assert.deepEqual(detailBlocker, {
  type: 'agent',
  targetType: 'agent',
  targetId: 'recheck-assistant',
  agentId: 'recheck-assistant',
  label: '回检填报助手',
  model: '项目默认模型',
  purpose: '解释回检状态并给出下一步建议',
  modelId: 'default-model',
  apiReferences: agent.apiReferences,
  entityIds: ['recheck-record'],
  knowledgeReferences: ['knowledge:recheck-policy']
})

assert.deepEqual(
  agentDevelopment.missingAgentEntityIds(agent, [
    {
      entityId: 'recheck-record',
      label: '回检单',
      designed: false,
      hasDetailPlan: false,
      detailPlanStatus: 'pending'
    }
  ]),
  ['recheck-record']
)
assert.deepEqual(
  agentDevelopment.missingAgentEntityIds(agent, [
    {
      entityId: 'recheck-record',
      label: '回检单',
      designed: true,
      hasDetailPlan: true,
      detailPlanStatus: 'confirmed'
    }
  ]),
  []
)

const designDoc = agentDevelopment.buildAgentDesignDoc(agent)
assert.match(designDoc, /^# 回检填报助手 智能体设计/m)
assert.deepEqual(designDoc.match(/^## .+$/gm), [
  '## 任务',
  '## 规则',
  '## 限制',
  '## 输入',
  '## 输出',
  '## 模型',
  '## 对话体验',
  '## 记忆',
  '## 工具',
  '## 知识检索'
])
assert.match(designDoc, /GET \/api\/rechecks\/my/)
assert.match(designDoc, /ep-my-rechecks/)
assert.match(designDoc, /recheck-record/)
assert.match(designDoc, /knowledge:recheck-policy/)
assert.match(designDoc, /只读当前用户可见数据/)
assert.match(designDoc, /人工确认/)

const configuredAgent = {
  model: {
    model: 'minimax-m2p5-229b-w8a8',
    deepThinking: true,
    temperature: 0.2,
    topP: 0.8,
    frequencyPenalty: 0.1,
    presencePenalty: 0.3,
    maxTokens: 2048,
    otherParameters: ['response_format=json']
  },
  personaReplyLogic: [
    '## 角色',
    '你是回检填报助手，负责解释回检状态。',
    '',
    '## 目标',
    '1. 帮助用户判断下一步处理方式。',
    '',
    '## 技能',
    '1. 为了查询状态，调用查询我的回检单。',
    '',
    '## 要求与限制',
    '1. 仅回答当前用户可见的数据。'
  ].join('\n'),
  skills: [
    {
      id: 'skill-creator',
      name: '技能创建器',
      description: '创建技能'
    }
  ],
  knowledge: [
    {
      id: 'knowledge:recheck-policy',
      name: 'recheck-policy',
      description: '回检策略'
    }
  ],
  tools: [
    {
      id: 'news',
      name: 'news',
      description: '查询新闻'
    }
  ],
  conversation: {
    multiTurn: false,
    toolEvidence: true,
    retryOnFailure: false
  }
}
const configuredDesignDoc = agentDevelopment.buildAgentDesignDoc(agent, configuredAgent)
const initialConfig = agentConfig.createInitialAgentConfig()
assert.match(initialConfig.personaReplyLogic, /^## 角色/m)
assert.match(initialConfig.personaReplyLogic, /^## 目标/m)
assert.match(initialConfig.personaReplyLogic, /^## 技能/m)
assert.match(initialConfig.personaReplyLogic, /^## 要求与限制/m)
assert.deepEqual(
  agentConfig
    .changedAgentConfigSections(initialConfig, configuredAgent)
    .map((section) => section.label),
  ['模型', '技能', '知识检索', '工具', '对话体验', '人设与回复逻辑']
)
const clonedConfig = agentConfig.cloneAgentConfig(configuredAgent)
assert.equal(agentConfig.areAgentConfigsEqual(configuredAgent, clonedConfig), true)
clonedConfig.skills.push({ id: 'extra-skill', name: '额外技能', description: '额外技能' })
assert.equal(agentConfig.areAgentConfigsEqual(configuredAgent, clonedConfig), false)
const optimizedPersona = agentConfig.buildOptimizedAgentPersonaReplyLogic({
  label: agent.label,
  purpose: agent.purpose,
  tools: agent.tools,
  permissions: agent.permissions
})
assert.match(optimizedPersona, /回检填报助手/)
assert.match(optimizedPersona, /解释回检状态并给出下一步建议/)
assert.match(optimizedPersona, /查询我的回检单/)
assert.match(optimizedPersona, /^## 要求与限制/m)
const invalidConfig = { ...initialConfig }
delete invalidConfig.personaReplyLogic
assert.equal(agentConfig.isAgentConfigState(invalidConfig), false)
assert.match(configuredDesignDoc, /minimax-m2p5-229b-w8a8/)
assert.match(configuredDesignDoc, /深度思考：启用/)
assert.match(configuredDesignDoc, /maxTokens=2048/)
assert.match(configuredDesignDoc, /技能创建器/)
assert.match(configuredDesignDoc, /连续多轮对话：关闭/)
assert.match(configuredDesignDoc, /人设与回复逻辑/)
const configuredSource = agentDevelopment.buildAgentSource(agent, configuredAgent)
assert.match(configuredSource.content, /deep_thinking=True/)
assert.match(configuredSource.content, /temperature=0\.2/)
assert.match(configuredSource.content, /max_tokens=2048/)
assert.match(configuredSource.content, /skills=\["技能创建器"\]/)
assert.match(configuredSource.content, /retry_on_failure=False/)
assert.match(configuredSource.content, /persona_reply_logic="## 角色/)
const configuredToolSource = agentDevelopment.buildAgentToolAdapterSource(agent, configuredAgent)
assert.match(configuredToolSource.content, /已配置工具：查询我的回检单、news。/)

/** 使用 Python 编译器校验生成源码的语法，避免仅通过字符串断言掩盖缩进或字面量错误。 */
// eslint-disable-next-line @typescript-eslint/explicit-function-return-type
function assertPythonSyntax(content, label) {
  const result = spawnSync(
    'python3',
    ['-c', 'import sys; compile(sys.stdin.read(), sys.argv[1], "exec")', label],
    { input: content, encoding: 'utf8' }
  )
  assert.equal(
    result.status,
    0,
    `${label} 不是有效的 Python 源码：${result.stderr || result.error?.message || '未知错误'}`
  )
}

const sourceArtifact = agentDevelopment.buildAgentSource(agent)
assert.equal(sourceArtifact.filePath, 'agent-runtime/agents/recheck_assistant.py')
assert.match(sourceArtifact.content, /class RecheckAssistantAgent:/)
assert.match(sourceArtifact.content, /AgentDefinition\(/)
assert.match(sourceArtifact.content, /查询我的回检单/)
assert.match(sourceArtifact.content, /default-model/)
assert.match(sourceArtifact.content, /recheck-record/)
assert.match(sourceArtifact.content, /def chat\(/)
assert.doesNotMatch(sourceArtifact.content, /public final class/)
assert.doesNotMatch(sourceArtifact.content, /\.java\b/)
assertPythonSyntax(sourceArtifact.content, sourceArtifact.filePath)

const toolArtifact = agentDevelopment.buildAgentToolAdapterSource(agent)
assert.equal(toolArtifact.filePath, 'agent-runtime/tools/recheck_assistant_tools.py')
assert.match(toolArtifact.content, /class RecheckAssistantToolAdapter:/)
assert.match(toolArtifact.content, /ep-my-rechecks/)
assert.match(toolArtifact.content, /def query\(/)
assert.doesNotMatch(toolArtifact.content, /\.java\b/)
assertPythonSyntax(toolArtifact.content, toolArtifact.filePath)

const pageIntegrationArtifact = agentDevelopment.buildAgentPageIntegrationSource(agent)
assert.equal(
  pageIntegrationArtifact.filePath,
  'frontend/src/pages/my-rechecks/components/RecheckAssistant.tsx'
)
assert.match(pageIntegrationArtifact.content, /回检填报助手/)

const firstTrial = agentDevelopment.createAgentTrialTurn(
  agent,
  '我的待审核回检单下一步应该怎么处理？',
  1
)
const followUpTrial = agentDevelopment.createAgentTrialTurn(agent, '还有哪些事项需要注意？', 2)
const configuredTrial = agentDevelopment.createAgentTrialTurn(
  agent,
  '查询最新状态',
  3,
  configuredAgent
)
assert.equal(firstTrial.sequence, 1)
assert.equal(followUpTrial.sequence, 2)
assert.equal(firstTrial.userMessage, '我的待审核回检单下一步应该怎么处理？')
assert.equal(followUpTrial.userMessage, '还有哪些事项需要注意？')
assert.notEqual(firstTrial.assistantMessage, followUpTrial.assistantMessage)
assert.equal(followUpTrial.toolName, '查询我的回检单')
assert.equal(followUpTrial.endpoint, 'GET /api/rechecks/my?status=待审核')
assert.equal(configuredTrial.toolName, '查询我的回检单')
assert.match(configuredTrial.evidence, /minimax-m2p5-229b-w8a8/)

const designStateUrl = new URL('../src/renderer/src/mock/designState.ts', import.meta.url)
const designStateSource = await readFile(designStateUrl, 'utf8')
const designStateTranspiled = ts.transpileModule(designStateSource, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022
  },
  fileName: designStateUrl.pathname
})
globalThis.window = {}
const designStateModuleUrl = `data:text/javascript;base64,${Buffer.from(
  designStateTranspiled.outputText
).toString('base64')}`
const designState = await import(designStateModuleUrl)
designState.markAgentDesigned('recheck-assistant', 'version-1')
designState.markEntityDesigned('recheck-record', 'version-1')
assert.equal(designState.isAgentDesigned('recheck-assistant', 'version-1'), true)
assert.equal(designState.isEntityDesigned('recheck-record', 'version-1'), true)
assert.equal(designState.isAgentDesigned('recheck-assistant', 'version-2'), false)
assert.equal(designState.isEntityDesigned('recheck-record', 'version-2'), false)
designState.clearDesignState('version-1')
assert.equal(designState.isAgentDesigned('recheck-assistant', 'version-1'), false)
assert.equal(designState.isEntityDesigned('recheck-record', 'version-1'), false)

/** 校验智能体在最新单会话工作台中仍有完整的数据投影、开发目标和确认入口。 */
const workbenchPageSource = await readFile(
  new URL('../src/renderer/src/pages/WorkbenchPage.tsx', import.meta.url),
  'utf8'
)
const leftPanelSource = await readFile(
  new URL('../src/renderer/src/components/LeftPanel/LeftPanel.tsx', import.meta.url),
  'utf8'
)
const aiChatPanelSource = await readFile(
  new URL('../src/renderer/src/components/AiChatPanel/AiChatPanel.tsx', import.meta.url),
  'utf8'
)
const detailSelectorSource = await readFile(
  new URL('../src/renderer/src/components/DetailConfirmationPageSelector/index.tsx', import.meta.url),
  'utf8'
)
const workflowCardSource = await readFile(
  new URL(
    '../src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/index.tsx',
    import.meta.url
  ),
  'utf8'
)
assert.match(workbenchPageSource, /setDevelopmentPlanningAgents/)
assert.match(leftPanelSource, /developmentPlanningAgents/)
assert.match(aiChatPanelSource, /developmentPlanningAgents/)
assert.match(aiChatPanelSource, /kind: 'agent' as const/)
assert.match(detailSelectorSource, /selectedAgent/)
assert.match(workflowCardSource, /<AgentDependencyGate/)

console.log('agent-development tests passed')
