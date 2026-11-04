import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import type { ApplicationLifecycle, WorkflowRunPayload } from '../src/renderer/src/typings'
import { planningTaskBlock } from '../src/renderer/src/components/AiChatPanel/planningTaskAdmission'
import PlanningTaskNotice from '../src/renderer/src/components/AiChatPanel/components/PlanningTaskNotice'
import { workflowMessageContentForDisplay } from '../src/renderer/src/service/processStepHistory'
import { latestConversationFailure } from '../src/renderer/src/components/AiChatPanel/globalFallbackState'

/** 构造后端当前业务阶段，与手动切到哪个页面无关。 */
function lifecycle(stage: ApplicationLifecycle['initialization']['stage']): ApplicationLifecycle {
  return { application: { id: 'app', name: 'app' }, revision: 2, updatedAt: '2026-11-04T00:00:00Z',
    initialization: { stage, status: 'running', threadId: 'current-planning-thread' }, activeExecutions: {}, extensions: {} }
}

test('需求与计划阶段在开发输入区明确提示，并提供对应阶段入口', () => {
  for (const [stage, message, action, phase] of [
    ['generating_requirement_document', '需求阶段正在运行，请先完成或结束当前需求任务', '返回需求阶段', 'product'],
    ['generating_technical_plan', '计划阶段正在运行，请先完成或结束当前计划', '返回计划阶段', 'planning'],
    ['awaiting_technical_plan_confirmation', '计划阶段正在运行，请先完成或结束当前计划', '返回计划阶段', 'planning']
  ] as const) {
    const block = planningTaskBlock(lifecycle(stage))!
    assert.equal(block.phase, phase)
    assert.equal(block.message, message)
    const markup = renderToStaticMarkup(createElement(PlanningTaskNotice, { block, onReturn: () => undefined }))
    assert.match(markup, /role="status"/)
    assert.ok(markup.includes(message))
    assert.ok(markup.includes(action))
    assert.doesNotMatch(markup, /ApplicationLifecycleConflictError|自由对话执行失败/)
  }
})

test('结束修订或正常完成规划后，开发输入不再被旧阶段提示阻挡', () => {
  assert.equal(planningTaskBlock(lifecycle('ready_for_workbench')), undefined)
  assert.equal(planningTaskBlock(undefined), undefined)
  const failed = lifecycle('generating_technical_plan')
  failed.initialization.status = 'failed'
  assert.equal(planningTaskBlock(failed)?.message, '计划阶段尚未完成，请先完成或结束当前计划')
})

test('后端准入失败在消息正文与底部使用同一业务提示，不暴露传输层错误前缀', () => {
  for (const [stage, errorCode] of [
    ['generating_requirement_document', 'APPLICATION_REQUIREMENT_IN_PROGRESS'],
    ['generating_technical_plan', 'APPLICATION_PLAN_IN_PROGRESS']
  ] as const) {
    const current = lifecycle(stage)
    const message = planningTaskBlock(current)!.message
    const workflow: WorkflowRunPayload = { runId: 'blocked', threadId: 'development', events: [],
      summary: { status: 'failed', phase: 'conversation', errorCode, message, lifecycle: current } }
    assert.equal(workflowMessageContentForDisplay(`自由对话执行失败：ApplicationPlanningInProgressError: ${message}`, workflow, false), message)
    assert.equal(latestConversationFailure([{ id: 1, role: 'assistant', content: '', workflow }]), message)
  }
})
