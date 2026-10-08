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
  recovery?: ApplicationPlanningRecoveryProjection
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
      conversationRunning: false, designPhasePlanning: true, loading: false,
      planningState: recovery ? {
        workflow, recovery, transportState: 'idle', connection: { status: 'healthy', requestGeneration: 1 }
      } as ApplicationPlanningCurrentState : undefined,
      messages: [{ id: 1, role: 'assistant', content: '', createdAt: 1, workflow }],
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

/** 当前执行中断必须保留原进度卡，但不能伪装成仍在运行或影响其他执行。 */
test('当前规划中断显示原进度卡的暂停状态，其他运行不受影响', () => {
  const workflow = planningProgress('technical_planning_begin', 'failed')
  const recovery: ApplicationPlanningRecoveryProjection = {
    schemaVersion: 'application-planning-recovery.v1', classification: 'ready_to_continue',
    sourceRunId: workflow.runId, threadId: workflow.threadId, canContinue: true,
    userActionRequired: false, inputCommitted: true, reasonCode: 'INTERRUPTED_CONTINUE_READY',
    message: '已验证当前 checkpoint，可以继续执行。'
  }
  const markup = renderPlanningMessage(workflow, recovery)
  assert.match(markup, /技术规划已中断/)
  assert.match(markup, /anticon-pause-circle/)
  assert.doesNotMatch(markup, /anticon-loading/)
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
  assert.match(renderPlanningMessage(workflow, { ...recovery,
    failureDiagnostic: { ...recovery.failureDiagnostic!, sourceRunId: 'other-run' }
  }), /技术规划已中断/)
})
