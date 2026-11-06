import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { WorkflowRunPayload } from '../src/renderer/src/typings'
import { technicalPlanFromWorkflow, technicalPlanBaselineStatus } from '../src/renderer/src/components/AiChatPanel/planningArtifactRecovery'

test('本轮技术规划中断时旧确认基线不能冒充本轮已确认，新草稿仍按待确认展示', () => {
  const plan = { confirmation_status: 'confirmed' }
  const failed = { summary: { phase: 'technical_planning', status: 'failed' } } as WorkflowRunPayload
  assert.equal(technicalPlanBaselineStatus(failed, plan), '上一版 · 本轮已中断')
  assert.equal(technicalPlanBaselineStatus({ ...failed, summary: { ...failed.summary, status: 'running' } }, plan), '上一版 · 正在生成')
  assert.equal(technicalPlanBaselineStatus(failed, { confirmation_status: 'pending_user_confirmation' }), undefined)
  assert.equal(technicalPlanBaselineStatus({ ...failed, summary: { ...failed.summary, phase: 'build' } }, plan), undefined)
})

test('技术规划空状态不能遮住结果中的架构或阻止正式文件读取', () => {
  const plan = { architecture: { frontend: 'React', backend: 'Java', data: 'MySQL' } }
  const workflow = {
    state: { technical_plan: {} },
    result: { technical_plan: plan }
  } as unknown as WorkflowRunPayload
  assert.equal(technicalPlanFromWorkflow(workflow), plan)
  assert.equal(technicalPlanFromWorkflow({ ...workflow, result: undefined }), undefined)
  assert.equal(technicalPlanFromWorkflow({ ...workflow, state: {
    technical_plan: { architecture: {} }
  }, result: undefined }), undefined)
  assert.equal(technicalPlanFromWorkflow(undefined), undefined)
  assert.equal(technicalPlanFromWorkflow({ ...workflow, state: { technical_plan: plan } }), plan)
})
