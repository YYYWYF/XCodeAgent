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

const agent = {
  id: 'recheck-assistant',
  label: '回检填报助手',
  purpose: '解释回检状态并给出下一步建议',
  model: '项目默认模型',
  apiDependencies: ['GET /api/rechecks/my'],
  pageIds: ['my-rechecks'],
  tools: ['查询我的回检单'],
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
  purpose: '解释回检状态并给出下一步建议'
})

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
assert.match(designDoc, /只读当前用户可见数据/)
assert.match(designDoc, /人工确认/)

const sourceArtifact = agentDevelopment.buildAgentSource(agent)
assert.equal(
  sourceArtifact.filePath,
  'backend/src/main/java/com/xcodeagent/generated/agent/RecheckAssistantAgent.java'
)
assert.match(sourceArtifact.content, /public final class RecheckAssistantAgent/)
assert.match(sourceArtifact.content, /AgentDefinition\.builder\(\)/)
assert.match(sourceArtifact.content, /查询我的回检单/)
assert.doesNotMatch(sourceArtifact.content, /createApplicationAgent/)

const firstTrial = agentDevelopment.createAgentTrialTurn(
  agent,
  '我的待审核回检单下一步应该怎么处理？',
  1
)
const followUpTrial = agentDevelopment.createAgentTrialTurn(agent, '还有哪些事项需要注意？', 2)
assert.equal(firstTrial.sequence, 1)
assert.equal(followUpTrial.sequence, 2)
assert.equal(firstTrial.userMessage, '我的待审核回检单下一步应该怎么处理？')
assert.equal(followUpTrial.userMessage, '还有哪些事项需要注意？')
assert.notEqual(firstTrial.assistantMessage, followUpTrial.assistantMessage)
assert.equal(followUpTrial.toolName, '查询我的回检单')
assert.equal(followUpTrial.endpoint, 'GET /api/rechecks/my?status=待审核')

console.log('agent-development tests passed')
