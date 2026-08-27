import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import ts from 'typescript'

const clarificationQuestions = JSON.parse(
  await readFile(
    new URL('../mock-data/pms-new/clarification-questions.json', import.meta.url),
    'utf8'
  )
)
const requirementSpec = JSON.parse(
  await readFile(new URL('../mock-data/pms-new/requirement-spec.json', import.meta.url), 'utf8')
)
const projectPlan = JSON.parse(
  await readFile(new URL('../mock-data/pms-new/project-plan.json', import.meta.url), 'utf8')
)

const agentQuestion = clarificationQuestions.find((question) => question.id === 'agent-capability')
assert.ok(agentQuestion, '新建应用需求问答必须包含智能体能力问题')
assert.equal(agentQuestion.presetAnswer, 'recheck-assistant')
assert.ok(
  agentQuestion.options.some((option) => option.value === 'no-agent'),
  '智能体问题必须允许用户明确选择暂不配置智能体'
)

const requirementAgent = requirementSpec.agents.find((agent) => agent.id === 'recheck-assistant')
assert.ok(requirementAgent, 'RequirementSpec 必须包含回检填报助手')
assert.deepEqual(requirementAgent.pages, ['my-rechecks'])
assert.ok(requirementAgent.boundaries.length >= 3)

const planningAgent = projectPlan.agents.find((agent) => agent.id === 'recheck-assistant')
assert.ok(planningAgent, 'ProjectPlan 必须包含回检填报助手')
assert.equal(planningAgent.modelId, 'default-model')
assert.equal(planningAgent.apiReferences[0].endpointId, 'ep-my-rechecks')
assert.deepEqual(planningAgent.entityIds, ['recheck-record'])
assert.ok(projectPlan.agent_acceptance_criteria.length >= 3)

const workbenchArtifactsUrl = new URL('../src/renderer/src/workbenchArtifacts.ts', import.meta.url)
const workbenchArtifactsSource = (await readFile(workbenchArtifactsUrl, 'utf8')).replace(
  "import { backendControllerPath, frontendPagePath } from './mock/workspaceFiles'",
  "const backendControllerPath = (...parts: string[]): string => parts.join('/'); const frontendPagePath = (...parts: string[]): string => parts.join('/')"
)
const transpiledArtifacts = ts.transpileModule(workbenchArtifactsSource, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022
  },
  fileName: workbenchArtifactsUrl.pathname
})
const workbenchArtifacts = await import(
  `data:text/javascript;base64,${Buffer.from(transpiledArtifacts.outputText).toString('base64')}`
)

const requirementMarkdown = workbenchArtifacts.buildRequirementSpecDoc(
  requirementSpec,
  '武汉分行需求回检系统'
)
assert.match(requirementMarkdown, /## 智能体需求/)
assert.match(requirementMarkdown, /回检填报助手/)
assert.match(requirementMarkdown, /连续多轮消息/)
assert.match(requirementMarkdown, /只读取当前用户可见的回检单/)

const projectPlanMarkdown = workbenchArtifacts.buildProjectPlanDoc(
  projectPlan,
  '武汉分行需求回检系统'
)
assert.match(projectPlanMarkdown, /## 智能体/)
assert.match(projectPlanMarkdown, /default-model/)
assert.match(projectPlanMarkdown, /ep-my-rechecks/)
assert.match(projectPlanMarkdown, /knowledge:recheck-policy/)
assert.match(projectPlanMarkdown, /## 智能体集成验收/)

console.log('new-app agent planning mock tests passed')
