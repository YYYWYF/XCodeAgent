import assert from 'node:assert/strict'
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
assert.deepEqual(
  agentConfig
    .changedAgentConfigSections(initialConfig, configuredAgent)
    .map((section) => section.label),
  ['模型', '技能', '知识检索', '工具', '对话体验']
)
const clonedConfig = agentConfig.cloneAgentConfig(configuredAgent)
assert.equal(agentConfig.areAgentConfigsEqual(configuredAgent, clonedConfig), true)
clonedConfig.skills.push({ id: 'extra-skill', name: '额外技能', description: '额外技能' })
assert.equal(agentConfig.areAgentConfigsEqual(configuredAgent, clonedConfig), false)
assert.match(configuredDesignDoc, /minimax-m2p5-229b-w8a8/)
assert.match(configuredDesignDoc, /深度思考：启用/)
assert.match(configuredDesignDoc, /maxTokens=2048/)
assert.match(configuredDesignDoc, /技能创建器/)
assert.match(configuredDesignDoc, /连续多轮对话：关闭/)
const configuredSource = agentDevelopment.buildAgentSource(agent, configuredAgent)
assert.match(configuredSource.content, /\.deepThinking\(true\)/)
assert.match(configuredSource.content, /\.temperature\(0\.2\)/)
assert.match(configuredSource.content, /\.maxTokens\(2048\)/)
assert.match(configuredSource.content, /\.skills\(List\.of\("技能创建器"\)\)/)
assert.match(configuredSource.content, /\.retryOnFailure\(false\)/)
const configuredToolSource = agentDevelopment.buildAgentToolAdapterSource(agent, configuredAgent)
assert.match(configuredToolSource.content, /已配置工具：查询我的回检单、news。/)

const sourceArtifact = agentDevelopment.buildAgentSource(agent)
assert.equal(
  sourceArtifact.filePath,
  'backend/src/main/java/com/xcodeagent/generated/agent/RecheckAssistantAgent.java'
)
assert.match(sourceArtifact.content, /public final class RecheckAssistantAgent/)
assert.match(sourceArtifact.content, /AgentDefinition\.builder\(\)/)
assert.match(sourceArtifact.content, /查询我的回检单/)
assert.match(sourceArtifact.content, /default-model/)
assert.match(sourceArtifact.content, /recheck-record/)
assert.doesNotMatch(sourceArtifact.content, /createApplicationAgent/)

const toolArtifact = agentDevelopment.buildAgentToolAdapterSource(agent)
assert.equal(
  toolArtifact.filePath,
  'backend/src/main/java/com/xcodeagent/generated/tool/RecheckAssistantToolAdapter.java'
)
assert.match(toolArtifact.content, /public final class RecheckAssistantToolAdapter/)
assert.match(toolArtifact.content, /ep-my-rechecks/)

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

console.log('agent-development tests passed')
