import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import MessageList from '../src/renderer/src/components/AiChatPanel/components/MessageList'
import { WorkbenchPhaseContext } from '../src/renderer/src/context/workbenchPhaseState'
import { WORKBENCH_PHASE_AGENTS } from '../src/renderer/src/workbenchPhase'
import type { WorkflowRunPayload } from '../src/renderer/src/typings'
import type { ApplicationPlanningCurrentState } from '../src/renderer/src/service/activeApplicationPlanning'
import type { ApplicationPlanningRecoveryProjection } from '../src/renderer/src/service/applicationPlanningRecovery'
import type { AgentChatMessage } from '../src/renderer/src/components/AiChatPanel/types'

import { appendPlanningEndMessage } from '../src/renderer/src/components/AiChatPanel/components/MessageList/planningEndMessage'

test('停止回执追加在历史卡后，保留正文、提交结果并在冷绑定时去重', () => {
  const workflow: WorkflowRunPayload = {
    runId: 'stopped-run', threadId: 'planning-thread', events: [],
    summary: { status: 'completed', phase: 'plan_control', message: '本轮任务已结束，已有文件已保留。',
      planControl: { action: 'end', status: 'ended', targetRunId: 'stopped-run' } }
  }
  const history: AgentChatMessage[] = [{
    id: 1, role: 'assistant', createdAt: 1, content: '原技术规划说明',
    workflow: { ...workflow, summary: { status: 'requires_user_input' } }, workflowCardResult: 'submitted'
  }, { id: 2, role: 'assistant', createdAt: 2, content: '', planningLoading: true }]
  const next = appendPlanningEndMessage(history, workflow, 3)
  assert.equal(next.length, 3)
  assert.equal(next[0], history[0])
  assert.equal(next[1].planningLoading, false)
  assert.equal(history[1].planningLoading, true)
  assert.equal(next[2].content, workflow.summary.message)
  assert.equal(appendPlanningEndMessage(next, workflow, 4), next)
  assert.equal(appendPlanningEndMessage(next, { ...workflow, runId: 'other-run' }, 5).length, 4)
})

/** 渲染测试不执行任何用户动作。 */
function ignoreAction(): void {}

/** 渲染测试不提交规划确认。 */
async function ignoreConfirmation(): Promise<void> {}

/** 构造与后端运行中快照相同的子节点完成消息。 */
function planningProgress(phase: string, status: string): WorkflowRunPayload {
  return {
    runId: 'planning-run',
    threadId: 'planning-thread',
    summary: { status, phase },
    events: [{
      id: 'completed-node', protocol: 'devagentstudio.workflow.event.v1', sequence: 1,
      type: 'workflow.node.completed', nodeName: phase, status: 'completed',
      runId: 'planning-run', threadId: 'planning-thread', message: '',
      node: { id: phase, label: '技术规划' }
    }],

    state: { ...({ status, phase }), ...({ status, phase }) }
  }
}

/** 用现有消息列表与阶段上下文检查原卡片的实际渲染结果。 */
function renderPlanningMessage(
  workflow: WorkflowRunPayload,
  recovery?: ApplicationPlanningRecoveryProjection,
  revisionHandoff?: AgentChatMessage['revisionHandoff'],
  executionRunning = workflow.summary.status === 'running',
  withoutHistory = false,
  history?: AgentChatMessage[]
): string {
  return renderToStaticMarkup(createElement(
    WorkbenchPhaseContext.Provider,
    { value: {
      phase: 'planning', derivedPhase: 'planning', reachedPhase: 'planning',
      manualOverride: null, locked: false, agent: WORKBENCH_PHASE_AGENTS.planning,
      recordReachedPhase: ignoreAction, switchPhase: ignoreAction,
      /** 测试只检查展示，不开放正式工件编辑。 */
      canEdit: () => false
    } },
    createElement(MessageList, {
      conversationRunning: false, designPhasePlanning: true, loading: executionRunning,
      planningState: {
        workflow, recovery, transportState: executionRunning ? 'running' : 'idle', connection: { status: 'healthy', requestGeneration: 1 }
      } as ApplicationPlanningCurrentState,
      messages: history ?? (withoutHistory ? [] : [{ id: 1, role: 'assistant', content: '', createdAt: 1, workflow, revisionHandoff }]),
      revertingCodeChangeIds: new Set<string>(),
      onRevertCodeChanges: ignoreAction, onOpenCodeChangeFile: ignoreAction,
      onSubmitClarification: ignoreConfirmation,
      onReviseConfirmedTechnicalPlan: ignoreAction
    })
  ))
}

/** 子节点完成事件不能让正在生成的技术规划会话变成空白。 */
test('技术规划子节点完成后继续显示原进度卡', () => {
  for (const phase of ['technical_planning_begin', 'technical_planning_generate']) {
    const markup = renderPlanningMessage(planningProgress(phase, 'running'))
    assert.match(markup, /正在生成技术规划/)
    assert.match(markup, /planning-workflow-activity/)
    assert.match(markup, /anticon-loading/)
  }
})

/** 模板重试不能被仍停留在技术规划确认的 lifecycle 误报为重新生成规划。 */
test('模板重试显示真实模板阶段，保留已确认规划的生命周期', () => {
  const workflow = planningProgress('template_reconcile', 'running')
  workflow.state = { lifecycle: { initialization: {
    stage: 'awaiting_technical_plan_confirmation', status: 'failed'
  } } }
  const markup = renderPlanningMessage(workflow)
  assert.match(markup, /正在更新应用模板/)
  assert.doesNotMatch(markup, /正在生成技术规划/)
})

/** 结束修订恢复开发准入，不代表本次候选已确认或模板已重新生成。 */
test('规划结束回执不展示模板成功卡或继续生成入口', () => {
  const workflow = planningProgress('plan_control', 'completed')
  workflow.summary.planControl = { action: 'end', status: 'ended', targetRunId: workflow.runId }
  workflow.summary.lifecycle = { application: { id: 'app', name: 'app' }, revision: 2,
    updatedAt: '2026-11-03T00:00:00Z', activeExecutions: {}, extensions: {},
    initialization: { stage: 'ready_for_workbench', status: 'completed', threadId: workflow.threadId } }
  const markup = renderPlanningMessage(workflow)
  assert.doesNotMatch(markup, /应用模板已就绪|技术规划已确认|继续生成/)
})

/** 已提交卡收成历史回执，停止回执单独提供新的修改入口。 */
test('停止后保留已提交技术规划卡并新增可修改卡', () => {
  const historical = planningProgress('technical_planning', 'requires_user_input')
  historical.summary.clarification = {
    mode: 'technical_plan_confirmation', status: 'requires_user_input', questions: []
  }
  const ended = planningProgress('plan_control', 'completed')
  ended.summary.message = '本轮任务已结束，已有文件已保留。'
  ended.summary.planControl = { action: 'end', status: 'ended', targetRunId: ended.runId }
  const markup = renderPlanningMessage(ended, undefined, undefined, false, false, [
    { id: 1, role: 'assistant', content: '', createdAt: 1, workflow: historical, workflowCardResult: 'submitted' },
    { id: 2, role: 'user', content: '技术规划确认：保留记录时间', createdAt: 2 },
    { id: 3, role: 'assistant', content: ended.summary.message, createdAt: 3, workflow: ended }
  ])
  assert.match(markup, /已提交/)
  assert.match(markup, /技术规划已提交/)
  assert.match(markup, /本轮任务已结束/)
  assert.match(markup, /已有技术规划已保留/)
  assert.match(markup, /可继续修改/)
  // 历史回执不保留禁用按钮；结束回执只有新的修改入口，没有旧确认操作。
  assert.doesNotMatch(markup, /disabled=""[^>]*><span>修\s*改/)
  assert.match(markup, /<button type="button" class="ant-btn ant-btn-default xa-requirement-spec-edit-btn"><span>修\s*改/)
})

/** 规划中断由底部恢复区域承载，空进度消息连同 Agent 头像一起隐藏。 */
test('当前规划中断不显示进度卡和孤立 Agent 头像，运行中仍显示进度', () => {
  const workflow = planningProgress('technical_planning_begin', 'failed')
  const recovery: ApplicationPlanningRecoveryProjection = {
    schemaVersion: 'application-planning-recovery.v1', classification: 'ready_to_continue',
    sourceRunId: workflow.runId, threadId: workflow.threadId, canContinue: true,
    userActionRequired: false, inputCommitted: true, reasonCode: 'INTERRUPTED_CONTINUE_READY',
    message: '已验证当前 checkpoint，可以继续执行。'
  }
  const markup = renderPlanningMessage(workflow, recovery)
  assert.doesNotMatch(markup, /技术规划已中断|planning-workflow-activity|规划 Agent|anticon-loading/)
  const handoffMarkup = renderPlanningMessage(workflow, recovery, {
    kind: 'revision_planning', formalBranch: 'design_stage_revision', targetSessionId: 'planning-session',
    targetConversationThreadId: 'planning-thread', impactInteractionId: 'impact', request: '新增页面'
  })
  assert.match(handoffMarkup, /已转入独立技术规划会话/)
  assert.doesNotMatch(handoffMarkup, /技术规划已中断|ai-message-agent-avatar|规划 Agent/)
  assert.doesNotMatch(renderPlanningMessage(workflow, { ...recovery, sourceRunId: 'other-run' }), /技术规划已中断/)
  assert.doesNotMatch(renderPlanningMessage(workflow, { ...recovery, threadId: 'other-thread' }), /技术规划已中断/)
  assert.doesNotMatch(renderPlanningMessage(workflow, { ...recovery, classification: 'failed' }), /技术规划已中断/)
  assert.match(renderPlanningMessage(planningProgress('technical_planning_begin', 'running'), recovery), /正在生成技术规划/)
})

/** 真正完成和失败必须收掉原 loading，避免修复后持续转圈。 */
test('技术规划终态不残留 loading 卡', () => {
  for (const status of ['completed', 'failed']) {
    const markup = renderPlanningMessage(planningProgress('technical_planning', status))
    assert.doesNotMatch(markup, /anticon-loading/)
    assert.doesNotMatch(markup, /正在生成技术规划/)
  }
})

/** 空历史与旧 running 快照都不能冒充当前 Agent 在执行。 */
test('空会话和陈旧运行消息不产生恢复卡或 loading', () => {
  const workflow = planningProgress('technical_planning_generate', 'running')
  for (const withoutHistory of [false, true]) {
    const markup = renderPlanningMessage(workflow, undefined, undefined, false, withoutHistory)
    assert.doesNotMatch(markup, /anticon-loading|正在恢复|正在处理|正在生成技术规划|尚未进入/)
  }
  assert.match(renderPlanningMessage(workflow, undefined, undefined, true, true), /anticon-loading/)
})

/** 本机没有聊天历史时，后端的真实确认门禁仍提供正式确认操作。 */
test('无本地会话仍展示后端当前确认操作', () => {
  const workflow = planningProgress('technical_planning', 'requires_user_input')
  workflow.summary.clarification = {
    mode: 'technical_plan_confirmation', status: 'requires_user_input',
    questions: []
  }
  workflow.state = {
    application_planning_interrupt: {
      gateId: 'current-gate', artifact: 'technical_plan', artifactRevision: 'current-revision'
    }
  }
  const markup = renderPlanningMessage(workflow, undefined, undefined, false, true)
  assert.match(markup, /确认技术规划/)
  assert.doesNotMatch(markup, /anticon-loading|正在恢复|暂无会话记录/)
  assert.doesNotMatch(markup, /<button[^>]*disabled[^>]*>[^<]*确认技术规划/)
})

/** 旧聊天不能遮住当前门禁，也不能继续提交已过期的确认。 */
test('历史消息未回填时当前后端确认仍独立可用且只出现一次', () => {
  const current = planningProgress('technical_planning', 'requires_user_input')
  current.summary.clarification = {
    mode: 'technical_plan_confirmation', status: 'requires_user_input', questions: []
  }
  current.state = { application_planning_interrupt: {
    gateId: 'new-gate', artifact: 'technical_plan', artifactRevision: 'new-revision'
  } }
  const old = { ...current, runId: 'old-run', state: { application_planning_interrupt: {
    gateId: 'old-gate', artifact: 'technical_plan', artifactRevision: 'old-revision'
  } } }
  for (const history of [
    [{ id: 1, role: 'user', content: '之前的消息', createdAt: 1 }],
    [{ id: 1, role: 'assistant', content: '', createdAt: 1, workflow: old }],
    [{ id: 1, role: 'assistant', content: '', createdAt: 1, workflow: current }]
  ] as AgentChatMessage[][]) {
    const markup = renderPlanningMessage(current, undefined, undefined, false, false, history)
    assert.equal((markup.match(/data-testid="current-planning-confirmation"/g) || []).length, 1)
    const currentMarkup = markup.slice(markup.indexOf('data-testid="current-planning-confirmation"'))
    assert.match(currentMarkup, /确认技术规划/)
    assert.doesNotMatch(currentMarkup, /<button[^>]*disabled/)
    if (history[0].workflow === current) {
      assert.equal((markup.match(/确认技术规划/g) || []).length, 1)
    }
  }
})

/** 模板故障只进入底部控制面，不能把已完成规划误报为中断；其他运行保持隔离。 */
test('当前模板失败不显示技术规划中断卡', () => {
  const workflow = planningProgress('technical_planning', 'failed')
  const recovery: ApplicationPlanningRecoveryProjection = {
    schemaVersion: 'application-planning-recovery.v1', classification: 'ready_to_continue',
    sourceRunId: workflow.runId, threadId: workflow.threadId, canContinue: true,
    userActionRequired: false, inputCommitted: true, reasonCode: 'FAILURE_RETRY_READY',
    message: '可以重试失败节点。',
    failureDiagnostic: { sourceRunId: workflow.runId, origin: 'external_dependency',
      code: 'templateengineerror', dependency: 'template_engine',
      operation: 'template_reconcile', message: 'Template Engine 地址未配置。' }
  }
  assert.doesNotMatch(renderPlanningMessage(workflow, recovery), /技术规划已中断|anticon-loading/)
  assert.doesNotMatch(renderPlanningMessage(workflow, { ...recovery,
    failureDiagnostic: { ...recovery.failureDiagnostic!, sourceRunId: 'other-run' }
  }), /技术规划已中断/)
})
