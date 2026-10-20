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
    result: { status, phase },
    state: { status, phase }
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
      onSubmitClarification: ignoreConfirmation
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
  assert.match(markup, /确认保存/)
  assert.doesNotMatch(markup, /anticon-loading|正在恢复|暂无会话记录/)
  assert.doesNotMatch(markup, /<button[^>]*disabled[^>]*>[^<]*确认保存/)
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
    assert.match(currentMarkup, /确认保存/)
    assert.doesNotMatch(currentMarkup, /<button[^>]*disabled/)
    if (history[0].workflow === current) {
      assert.equal((markup.match(/确认保存/g) || []).length, 1)
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
