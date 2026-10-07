import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement, Fragment } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { WorkbenchPhaseContext } from '../src/renderer/src/context/workbenchPhaseState'
import RecoveryIncidentCard from '../src/renderer/src/components/RecoveryIncidentCard/RecoveryIncidentCard'
import ApplicationPagePlanningModal from '../src/renderer/src/components/Welcome/ApplicationPagePlanningModal'
import AgentErrorCard from '../src/renderer/src/components/AgentErrorCard'
import ApplicationPlanningRecoveryIncidentCard from '../src/renderer/src/components/ApplicationPlanningRecoveryIncidentCard'
import ConnectionStatusBanner from '../src/renderer/src/components/ConnectionStatusBanner'
import MessageList from '../src/renderer/src/components/AiChatPanel/components/MessageList'
import RecoverySurface from '../src/renderer/src/components/AiChatPanel/recoverySurface'
import { WORKBENCH_PHASE_AGENTS } from '../src/renderer/src/workbenchPhase'
import { applicationPlanningRecoveryProjection } from '../src/renderer/src/service/applicationPlanningRecovery'
import type { ApplicationPlanningCurrentState } from '../src/renderer/src/service/activeApplicationPlanning'
import { initialConnectionState } from '../src/renderer/src/service/connectionState'
import { workbenchRecoveryIncident } from '../src/renderer/src/service/recoveryIncident'
import { recoveryFailureMessage } from '../src/renderer/src/service/recoveryFailureMessage'
import type { ExecutionRecoveryCandidate, WorkflowRunPayload } from '../src/renderer/src/typings'
import { workflowClarification } from '../src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/workflowClarification'

test('单测恢复仅在同 Run 正在执行且无待确认交互时隐藏旧确认', () => {
  const workflow = {
    runId: 'run-unit', threadId: 'thread-unit', events: [],
    summary: {
      status: 'running', phase: 'unit_test',
      clarification: { mode: 'unit_test_confirmation', questions: [] },
      lifecycle: { activeExecutions: { 'run-unit': {
        threadId: 'thread-unit', status: 'running', phase: 'unit_test', pendingInteraction: null
      } } }
    }
  } as unknown as WorkflowRunPayload
  assert.equal(workflowClarification(workflow), undefined)
  workflow.summary.phase = 'inspect_workspace'
  assert.equal(workflowClarification(workflow), undefined)
  workflow.summary.status = 'requires_user_input'
  assert.equal(workflowClarification(workflow)?.mode, 'unit_test_confirmation')
  workflow.summary.status = 'running'
  workflow.summary.lifecycle!.activeExecutions['run-unit'].threadId = 'other-thread'
  assert.equal(workflowClarification(workflow)?.mode, 'unit_test_confirmation')
  workflow.summary.lifecycle!.activeExecutions['run-unit'].threadId = 'thread-unit'
  workflow.summary.lifecycle!.activeExecutions['run-unit'].status = 'awaiting_user'
  assert.equal(workflowClarification(workflow)?.mode, 'unit_test_confirmation')
  workflow.summary.lifecycle!.activeExecutions['run-unit'].phase = 'build'
  workflow.summary.lifecycle!.activeExecutions['run-unit'].status = 'running'
  assert.equal(workflowClarification(workflow)?.mode, 'unit_test_confirmation')
})

/** 统计服务端文本在实际渲染结果中的出现次数。 */
function countOccurrences(markup: string, text: string): number {
  return markup.split(text).length - 1
}

/** 从 Backend-like Workflow 投影构造当前 Application Planning 状态。 */
function planningStateFromRecovery(options: {
  sourceRunId: string
  model: string
  diagnosticMessage: string
  status?: 'recoverable' | 'needs_attention'
}): ApplicationPlanningCurrentState {
  const status = options.status || 'recoverable'
  const workflow = {
    runId: options.sourceRunId,
    threadId: 'thread-A',
    summary: { status: 'failed', message: options.diagnosticMessage },
    events: [],
    state: {},
    result: {
      applicationPlanningRecovery: {
        schemaVersion: 'application-planning-recovery.v1',
        classification: status === 'recoverable' ? 'ready_to_continue' : 'blocked',
        sourceRunId: options.sourceRunId,
        threadId: 'thread-A',
        canContinue: status === 'recoverable',
        userActionRequired: false,
        inputCommitted: true,
        reasonCode:
          status === 'recoverable' ? 'RECOVERABLE' : 'NATIVE_SUBGRAPH_REPLAY_UNSUPPORTED',
        message:
          status === 'recoverable'
            ? '当前 checkpoint 可以安全恢复。'
            : '当前现场没有可证明安全的自动恢复入口，需要人工处理。',
        failureDiagnostic: {
          sourceRunId: options.sourceRunId,
          origin: 'model_call',
          code: 'MODEL_NOT_FOUND',
          operation: 'technical_planning',
          provider: 'openai-compatible',
          model: options.model,
          httpStatus: 404,
          message: options.diagnosticMessage
        },
        recoveryActionPlan: {
          schemaVersion: 'recovery-action-plan.v1',
          incidentId: `incident-${options.sourceRunId}`,
          sourceRunId: options.sourceRunId,
          threadId: 'thread-A',
          executionKind: 'application_planning',
          status,
          reasonCode:
            status === 'recoverable'
              ? 'TECHNICAL_PLAN_STAGE_RESTART'
              : 'NATIVE_SUBGRAPH_REPLAY_UNSUPPORTED',
          message:
            status === 'recoverable'
              ? '已验证正式规划输入，可以重新执行技术规划。'
              : '当前现场没有可证明安全的自动恢复入口，需要人工处理。',
          primaryAction:
            status === 'recoverable'
              ? {
                  actionId: `action-${options.sourceRunId}`,
                  kind: 'restart_stage',
                  label: '重新执行技术规划',
                  description: '从 Technical Planning 阶段重新执行。',
                  requiresConfirmation: false
                }
              : null,
          alternateActions: []
        }
      }
    }
  } as WorkflowRunPayload
  const recovery = applicationPlanningRecoveryProjection(workflow)
  if (!recovery) throw new Error('测试 Workflow 缺少有效的 Recovery Projection。')
  return {
    application: { id: 'app-A', appName: 'Demo App' },
    threadId: 'thread-A',
    connection: initialConnectionState(true),
    transportState: 'idle',
    error: options.diagnosticMessage,
    lifecycle: {
      application: { id: 'app-A', name: 'Demo App' },
      revision: 1,
      updatedAt: '2026-09-12T00:00:00.000Z',
      initialization: { status: 'failed', stage: 'generating_technical_plan' },
      activeExecutions: {},
      extensions: {}
    },
    workflow,
    recovery
  } as ApplicationPlanningCurrentState
}

/** 组合历史错误卡与唯一当前 Recovery Incident，模拟聊天 caller 的输出边界。 */
function renderPlanningRecoverySurface(
  historyErrors: string[],
  planning: ApplicationPlanningCurrentState
): string {
  return renderToStaticMarkup(
    createElement(
      Fragment,
      null,
      ...historyErrors.map((error, index) =>
        createElement(AgentErrorCard, { error, key: `history-${index}` })
      ),
      createElement(ApplicationPlanningRecoveryIncidentCard, {
        onAction: () => undefined,
        planning
      })
    )
  )
}

/** 构造恢复卡片所需的公开候选。 */
function recovery(
  availability: ExecutionRecoveryCandidate['availability'],
  executionKind: ExecutionRecoveryCandidate['executionKind'] = 'workbench'
): ExecutionRecoveryCandidate {
  return {
    sourceRunId: 'run-A',
    ownerSessionId: 'session-A',
    threadId: 'thread-A',
    executionKind,
    executionStatus: 'interrupted',
    availability,
    canContinue: availability === 'ready',
    reasonCode: 'RECOVERY_TEST',
    message: '恢复测试',
    failureDiagnostic: {
      sourceRunId: 'run-A',
      origin: 'model_call',
      code: 'WORKBENCH_RECOVERY_DIAGNOSTIC',
      operation: 'failed_node',
      message: 'Workbench diagnostic visible'
    },
    recoveryActionPlan: {
      schemaVersion: 'recovery-action-plan.v1',
      incidentId: 'incident-run-A',
      sourceRunId: 'run-A',
      threadId: 'thread-A',
      executionKind,
      status: availability === 'ready' ? 'recoverable' : 'needs_attention',
      reasonCode: 'RECOVERY_TEST',
      message: '恢复测试',
      primaryAction:
        availability === 'ready'
          ? {
              actionId: 'action-run-A',
              kind: 'continue_checkpoint',
              label: '继续执行',
              description: '从保存的现场继续执行。',
              requiresConfirmation: false
            }
          : null,
      alternateActions: []
    },
    updatedAt: '2026-09-12T00:00:00.000Z'
  }
}

/** 渲染真实聊天 caller 使用的 Recovery surface，验证两个当前控制面的边界。 */
function renderRecoverySurface(options: {
  isApplicationPlanningPhase: boolean
  planning?: ApplicationPlanningCurrentState
  candidate?: ExecutionRecoveryCandidate
}): string {
  return renderToStaticMarkup(
    createElement(RecoverySurface, {
      activeExecutionRecovery: options.candidate,
      isApplicationPlanningPhase: options.isApplicationPlanningPhase,
      onExecuteRecoveryAction: () => undefined,
      onRetryPlanning: () => undefined,
      planningState: options.planning,
      recoveryRunning: false
    })
  )
}

test('中文诊断按明确错误码映射，未知原因不猜测', () => {
  for (const [code, httpStatus, expected] of [
    ['UNIT_GENERATION_MODEL_REQUEST_TIMEOUT', null, '模型请求超时'],
    ['UNIT_GENERATION_MODEL_CONNECTION_FAILED', null, '无法连接模型服务'],
    ['UNIT_GENERATION_MODEL_SETUP_FAILED', null, '模型调用准备失败'],
    ['UNIT_GENERATION_MODEL_RESPONSE_INVALID', null, '数据无法读取'],
    ['UNIT_GENERATION_MODEL_HTTP_ERROR', 401, '认证失败'],
    ['UNIT_GENERATION_MODEL_HTTP_ERROR', 429, '限制了本次请求'],
    ['UNIT_GENERATION_MODEL_HTTP_ERROR', 503, '暂时无法完成请求'],
    ['UNIT_GENERATION_MODEL_HTTP_ERROR', 200, '未能确定具体原因']
  ] as const) {
    assert.ok(recoveryFailureMessage({
      sourceRunId: 'run-A', origin: 'model_call', code, httpStatus
    })?.includes(expected))
  }
  assert.equal(recoveryFailureMessage({
    sourceRunId: 'run-A', origin: 'unknown', code: 'NEW_UNKNOWN'
  }), undefined)
})

test('当前规划中文摘要展示在卡片，轮次原因留在展开详情', () => {
  const candidate = recovery('ready')
  candidate.executionStatus = 'failed'
  candidate.failureDiagnostic = {
    sourceRunId: candidate.sourceRunId, origin: 'unknown',
    code: 'GLOBAL_REPAIR_LIMIT_EXHAUSTED',
    userMessage: '生成执行计划失败：AI 生成的任务信息不完整，自动修复后仍未通过检查。请重试。',
    message: '第 1 轮：模型输出被截断。第 3 轮：生成的任务缺少必要的任务标识。'
  }
  const incident = workbenchRecoveryIncident(candidate)
  const markup = renderToStaticMarkup(createElement(RecoveryIncidentCard, { incident: incident! }))
  const [summary, details] = markup.split('<details')
  assert.match(summary, /AI 生成的任务信息不完整/)
  assert.doesNotMatch(summary, /第 1 轮|GLOBAL_REPAIR_LIMIT_EXHAUSTED/)
  assert.match(details, /模型输出被截断/)
  assert.match(details, /任务缺少必要的任务标识/)
})

test('底部保留同一失败 Run 的单测错误，拒绝其他 Run 的诊断', () => {
  const candidate = recovery('ready')
  candidate.executionStatus = 'failed'
  candidate.failureDiagnostic = {
    sourceRunId: candidate.sourceRunId, origin: 'unknown', code: 'UNIT_TEST_FAILED',
    message: '测试生成 Agent 检测到测试目录外实际写入：recovery.sqlite'
  }
  const incident = workbenchRecoveryIncident(candidate)!
  assert.equal(incident.failureMessage, candidate.failureDiagnostic.message)
  const markup = renderToStaticMarkup(createElement(RecoveryIncidentCard, { incident }))
  assert.match(markup.split('<details')[0], /测试生成 Agent 检测到测试目录外实际写入/)
  candidate.failureDiagnostic.sourceRunId = 'another-run'
  assert.equal(workbenchRecoveryIncident(candidate)!.failureMessage, undefined)
})

test('Workbench Recovery Incident exposes the Backend primary action', () => {
  const incident = workbenchRecoveryIncident(recovery('ready'))
  if (!incident) throw new Error('测试候选未生成 Workbench Incident。')
  const markup = renderToStaticMarkup(
    createElement(RecoveryIncidentCard, {
      incident,
      onAction: () => undefined
    })
  )
  assert.match(markup, /工作台执行需要恢复/)
  assert.match(markup, /继续执行/)
})

test('Build 失败重试按同 Run 任务摘要显示保留进度，其他 Run 数量不得混入', () => {
  const candidate = recovery('ready')
  candidate.executionStatus = 'failed'
  candidate.failureDiagnostic = undefined
  candidate.recoveryActionPlan.primaryAction = {
    actionId: 'retry-build', kind: 'retry_failed_node', targetNode: 'build',
    label: '重试', description: '重试 Build', requiresConfirmation: false
  }
  const workflow = {
    runId: candidate.sourceRunId, threadId: candidate.threadId,
    summary: { status: 'failed', message: 'Workflow failed：完成 2 个节点。',
      buildSummary: { completed: 2, retry_available: true } }
  } as WorkflowRunPayload
  const incident = workbenchRecoveryIncident(candidate, workflow)!
  assert.equal(incident.failureMessage, '开发任务执行失败。')
  assert.equal(incident.recoveryMessage, '已完成 2 个任务，将保留已完成进度，仅重试失败任务。')
  const otherRun = workbenchRecoveryIncident(candidate, { ...workflow, runId: 'other-run' })!
  assert.equal(otherRun.recoveryMessage, '将保留已完成进度，继续处理失败任务。')
  workflow.summary.buildSummary = { completed: 2, recovery_available: true }
  assert.match(workbenchRecoveryIncident(candidate, workflow)!.recoveryMessage, /继续执行失败任务的修复计划/)
  candidate.executionStatus = 'interrupted'
  candidate.recoveryActionPlan.primaryAction.kind = 'continue_checkpoint'
  assert.match(workbenchRecoveryIncident(candidate, workflow)!.recoveryMessage, /从开发实现阶段重新开始/)
  assert.doesNotMatch(workbenchRecoveryIncident(candidate, workflow)!.recoveryMessage, /保留已完成进度|已完成 2/)
})

test('failed node retry shows the entry node and preserves the current public failure', () => {
  const candidate = recovery('ready')
  candidate.executionStatus = 'failed'
  candidate.currentNode = 'prepare_build_tasks'
  candidate.recoveryActionPlan.primaryAction = {
    actionId: 'retry-run-A',
    kind: 'retry_failed_node',
    targetNode: 'prepare_build_tasks',
    label: '重新执行失败步骤',
    description: '从失败节点重新执行。',
    requiresConfirmation: false
  }
  const incident = workbenchRecoveryIncident(candidate)
  if (!incident) throw new Error('测试候选未生成 Workbench Incident。')
  const markup = renderToStaticMarkup(createElement(RecoveryIncidentCard, {
    incident,
    onAction: () => undefined
  }))
  assert.match(markup, /当前执行失败/)
  assert.match(markup, /将从「生成执行计划」节点重试。/)
  assert.match(markup, />重试</)
  assert.match(markup, /Workbench diagnostic visible/)
  assert.match(markup, /<summary>错误详情<\/summary>/)
  assert.doesNotMatch(markup, /PlanningRun|DagPlanningError/)
  assert.doesNotMatch(markup.split('<details')[0], /恢复目标：/)
})

test('Workbench current recovery shows a model failure only for the matching structured run', () => {
  const candidate = recovery('ready')
  candidate.executionStatus = 'failed'
  candidate.failureDiagnostic = undefined
  candidate.recoveryActionPlan.primaryAction = {
    actionId: 'retry-run-A',
    kind: 'retry_failed_node',
    targetNode: 'prepare_build_tasks',
    label: '重试',
    description: '从已验证入口重试。',
    requiresConfirmation: false
  }
  const workflow = {
    runId: candidate.sourceRunId,
    threadId: candidate.threadId,
    summary: {
      status: 'failed',
      phase: 'prepare_build_tasks',
      errorCode: 'UNIT_GENERATION_INFRASTRUCTURE_FAILURE'
    },
    events: []
  } as WorkflowRunPayload
  const matching = workbenchRecoveryIncident(candidate, workflow)
  const otherRun = workbenchRecoveryIncident(candidate, { ...workflow, runId: 'other-run' })
  const unknownCode = workbenchRecoveryIncident(candidate, {
    ...workflow,
    summary: { ...workflow.summary, errorCode: 'OTHER_FAILURE' }
  })
  assert.match(renderToStaticMarkup(createElement(RecoveryIncidentCard, {
    incident: matching!, onAction: () => undefined
  })), /生成执行计划时，模型准备或调用失败。/)
  assert.equal(otherRun?.failureMessage, undefined)
  assert.equal(unknownCode?.failureMessage, undefined)
  // Candidate 缺失也必须归到当前恢复卡，跨 Run/Thread 的历史错误不能混入。
  const missingCandidateWorkflow = {
    ...workflow,
    summary: {
      ...workflow.summary,
      errorCode: 'GLOBAL_CANDIDATE_MISSING',
      message: 'Unit backend:endpoint:cat_image_api:cat_image_api.list 本轮未产生有效 Candidate。'
    }
  }
  const missingCandidate = workbenchRecoveryIncident(candidate, missingCandidateWorkflow)
  assert.match(renderToStaticMarkup(createElement(RecoveryIncidentCard, {
    incident: missingCandidate!, onAction: () => undefined
  })), /cat_image_api.list 本轮未产生有效 Candidate。/)
  assert.equal(workbenchRecoveryIncident(candidate, {
    ...missingCandidateWorkflow, runId: 'other-run'
  })?.failureMessage, undefined)
  assert.equal(workbenchRecoveryIncident(candidate, {
    ...missingCandidateWorkflow, threadId: 'other-thread'
  })?.failureMessage, undefined)
})

test('Workbench uses the current source diagnostic after refresh and ignores an older run', () => {
  const candidate = recovery('ready')
  candidate.executionStatus = 'failed'
  candidate.recoveryActionPlan.primaryAction = {
    actionId: 'retry-run-R2', kind: 'retry_failed_node', targetNode: 'prepare_build_tasks',
    label: '重试', description: '从已验证入口重试。', requiresConfirmation: false
  }
  candidate.sourceRunId = 'run-R2'
  candidate.recoveryActionPlan.sourceRunId = 'run-R2'
  candidate.failureDiagnostic = {
    sourceRunId: 'run-R2', origin: 'model_call', code: 'UNIT_GENERATION_MODEL_HTTP_ERROR',
    httpStatus: 429, stage: 'model_invoke', message: '模型服务返回 HTTP 429。'
  }
  const oldWorkflow = {
    runId: 'run-R1', threadId: candidate.threadId,
    summary: {
      status: 'failed', errorCode: 'UNIT_GENERATION_MODEL_CALL_FAILED',
      failureDiagnostic: {
        sourceRunId: 'run-R1', origin: 'model_call', code: 'UNIT_GENERATION_MODEL_CALL_FAILED',
        message: '模型调用失败，未能确定具体原因。'
      }
    }, events: []
  } as WorkflowRunPayload
  const incident = workbenchRecoveryIncident(candidate, oldWorkflow)
  assert.equal(incident?.failureDiagnostic?.sourceRunId, 'run-R2')
  assert.equal(incident?.failureDiagnostic?.stage, 'model_invoke')
  assert.equal(incident?.failureMessage, '模型服务返回 HTTP 429。')
  const markup = renderToStaticMarkup(createElement(RecoveryIncidentCard, {
    incident: incident!, onAction: () => undefined
  }))
  assert.match(markup, /模型服务返回 HTTP 429。/)
  assert.match(markup, /失败阶段/)
  assert.match(markup, /model_invoke/)
  assert.doesNotMatch(markup, /模型调用失败，未能确定具体原因。/)
})

test('Workbench live diagnostic survives an older generic persisted failure', () => {
  const candidate = recovery('ready')
  candidate.executionStatus = 'failed'
  candidate.failureDiagnostic = {
    sourceRunId: candidate.sourceRunId, origin: 'unknown', code: 'dagplanningerror',
    message: 'generic terminal'
  }
  const workflow = {
    runId: candidate.sourceRunId, threadId: candidate.threadId,
    summary: {
      status: 'failed', errorCode: 'UNIT_GENERATION_MODEL_RESPONSE_INVALID',
      failureDiagnostic: {
        sourceRunId: candidate.sourceRunId, origin: 'model_call',
        code: 'UNIT_GENERATION_MODEL_RESPONSE_INVALID', httpStatus: 200,
        message: '模型服务返回的响应无法读取。'
      }
    }, events: []
  } as WorkflowRunPayload
  const incident = workbenchRecoveryIncident(candidate, workflow)
  assert.equal(incident?.failureMessage, '模型服务返回的响应无法读取。')
  assert.equal(incident?.failureDiagnostic?.httpStatus, 200)
  assert.notEqual(incident?.failureMessage, 'generic terminal')
})

test('消息区永远不渲染当前或历史通用错误卡', () => {
  const workflow = {
    runId: 'run-A',
    threadId: 'thread-A',
    summary: {
      status: 'failed',
      phase: 'prepare_build_tasks',
      errorCode: 'UNIT_GENERATION_INFRASTRUCTURE_FAILURE'
    },
    events: []
  } as WorkflowRunPayload
  const markup = renderToStaticMarkup(createElement(
    WorkbenchPhaseContext.Provider,
    {
      value: {
        phase: 'development',
        derivedPhase: 'development',
        reachedPhase: 'development',
        recordReachedPhase: () => undefined,
        manualOverride: null,
        switchPhase: () => undefined,
        agent: WORKBENCH_PHASE_AGENTS.development,
        canEdit: () => true
      }
    },
    createElement(MessageList, {
      applicationTemplatePreparationEligible: false,
      codeChangeActionsDisabled: false,
      conversationRunning: false,
      loading: false,
      messages: [{
        id: 1,
        role: 'assistant',
        content: '',
        error: 'Workflow failed：DagPlanningError: Unit Candidate 生成发生模型基础设施错误，PlanningRun 已终止。',
        createdAt: 1,
        workflow
      }, {
        id: 2,
        role: 'assistant',
        content: '',
        error: '普通任务失败',
        createdAt: 2,
        workflow: {
          ...workflow,
          runId: 'run-B',
          summary: { ...workflow.summary, errorCode: 'OTHER_FAILURE' }
        }
      }, {
        id: 3,
        role: 'assistant',
        content: '',
        error: 'Workflow failed：DagPlanningError: 本轮未产生有效 Candidate。',
        createdAt: 3,
        workflow: {
          ...workflow,
          runId: 'run-C',
          summary: { ...workflow.summary, errorCode: 'GLOBAL_CANDIDATE_MISSING' }
        }
      }],
      onOpenCodeChangeFile: () => undefined,
      onRevertCodeChanges: () => undefined,
      onSubmitClarification: async () => undefined,
      revertingCodeChangeIds: new Set()
    })
  ))
  assert.doesNotMatch(markup, /模型服务异常|请检查模型名称和服务地址/)
  assert.doesNotMatch(markup, /普通任务失败/)
  assert.doesNotMatch(markup, /本轮未产生有效 Candidate/)
  assert.equal(countOccurrences(markup, 'agent-error-card-title'), 0)
})

test('Workbench retry card does not repeat the last retry error', () => {
  const markup = renderToStaticMarkup(createElement(RecoverySurface, {
    activeExecutionRecovery: recovery('ready'),
    isApplicationPlanningPhase: false,
    onExecuteRecoveryAction: () => undefined,
    recoveryError: '上次重试没成功',
    recoveryRunning: false
  }))
  assert.doesNotMatch(markup, /上次重试没成功|重试未成功/)
})

test('Workbench needs_attention retains a retry entry for Backend re-resolution', () => {
  for (const availability of ['blocked', 'requires_handler'] as const) {
    const incident = workbenchRecoveryIncident(recovery(availability))
    if (!incident) throw new Error('测试候选未生成 Workbench Incident。')
    let actionCalls = 0
    const markup = renderToStaticMarkup(
      createElement(RecoveryIncidentCard, {
        incident,
        onAction: () => {
          actionCalls += 1
        }
      })
    )
    assert.doesNotMatch(markup, /RECOVERY_TEST/)
    assert.doesNotMatch(markup, /Workbench diagnostic visible/)
    assert.match(markup, /<button/)
    assert.match(markup, /重试/)
    assert.doesNotMatch(markup, /继续执行/)
    assert.equal(actionCalls, 0)
  }
})

test('stale recovery details stay internal while the existing retry remains available', () => {
  const candidate = recovery('blocked')
  candidate.recoveryActionPlan.reasonCode = 'RECOVERY_SOURCE_NOT_CURRENT'
  candidate.recoveryActionPlan.message =
    '中断现场缺少唯一、最新且可验证的 checkpoint，已阻止降级恢复。'
  const incident = workbenchRecoveryIncident(candidate)
  if (!incident) throw new Error('测试候选未生成 Workbench Incident。')
  const markup = renderToStaticMarkup(createElement(RecoveryIncidentCard, { incident }))
  assert.match(markup, /执行未完成/)
  assert.match(markup, /请重试；系统会重新确认执行起点。/)
  assert.doesNotMatch(markup, /checkpoint|RECOVERY_SOURCE_NOT_CURRENT|请先处理上方的任务错误/)
})

test('planning Recovery Incident shows safe failure summary and Backend action', () => {
  const markup = renderToStaticMarkup(
    createElement(ApplicationPlanningRecoveryIncidentCard, {
      planning: {
        application: { id: 'app-A', appName: 'Demo App' },
        threadId: 'thread-A',
        connection: initialConnectionState(true),
        transportState: 'idle',
        error: 'Model not found',
        lifecycle: {
          application: { id: 'app-A', name: 'Demo App' },
          revision: 1,
          updatedAt: '2026-09-12T00:00:00.000Z',
          initialization: { status: 'failed', stage: 'generating_technical_plan' },
          activeExecutions: {},
          extensions: {}
        },
        recovery: {
          schemaVersion: 'application-planning-recovery.v1',
          classification: 'ready_to_continue',
          sourceRunId: 'run-B',
          threadId: 'thread-A',
          canContinue: true,
          userActionRequired: false,
          inputCommitted: true,
          reasonCode: 'APPLICATION_PLANNING_MODEL_GENERATION_REPLAY_SAFE',
          message: '当前 checkpoint 已无法直接继续。',
          failureDiagnostic: {
            sourceRunId: 'run-B',
            origin: 'model_call',
            code: 'MODEL_NOT_FOUND',
            operation: 'technical_planning',
            provider: 'openai-compatible',
            model: 'mimo-v2.5-pro',
            httpStatus: 404,
            message: 'Model not found'
          },
          recoveryActionPlan: {
            schemaVersion: 'recovery-action-plan.v1',
            incidentId: 'incident-B',
            sourceRunId: 'run-B',
            threadId: 'thread-A',
            executionKind: 'application_planning',
            status: 'recoverable',
            reasonCode: 'TECHNICAL_PLAN_STAGE_RESTART',
            message: '已验证正式规划输入，可以重新执行技术规划。',
            primaryAction: {
              actionId: 'action-B',
              kind: 'restart_stage',
              label: '重新执行技术规划',
              description: '从 Technical Planning 阶段重新执行。',
              requiresConfirmation: false
            },
            alternateActions: []
          }
        }
      } as ApplicationPlanningCurrentState,
      onAction: () => undefined
    })
  )
  assert.match(markup, /HTTP 状态码[\s\S]*404/)
  assert.match(markup, /模型[\s\S]*mimo-v2\.5-pro/)
  assert.match(markup, /Model not found/)
  assert.match(markup, /错误详情/)
  assert.match(markup, /重新执行技术规划/)
  assert.match(markup, /已验证正式规划输入，可以重新执行技术规划/)
})

test('普通任务错误保留底部重试入口', () => {
  const markup = renderToStaticMarkup(
    createElement(AgentErrorCard, {
      error: '普通 Network Error',
      onRetry: () => undefined,
      retryLabel: '重试'
    })
  )
  assert.match(markup, /普通 Network Error/)
  assert.match(markup, /重试/)
  assert.doesNotMatch(markup, /重新执行技术规划/)
})

test('消息区不再显示历史恢复错误，下方保留唯一恢复入口', () => {
  const markup = renderToStaticMarkup(
    createElement(
      WorkbenchPhaseContext.Provider,
      {
        value: {
          phase: 'planning',
          derivedPhase: 'planning',
          reachedPhase: 'planning',
          recordReachedPhase: () => undefined,
          manualOverride: null,
          switchPhase: () => undefined,
          agent: WORKBENCH_PHASE_AGENTS.planning,
          canEdit: () => true
        }
      },
      createElement(
        Fragment,
        null,
        createElement(MessageList, {
          applicationTemplatePreparationEligible: false,
          codeChangeActionsDisabled: false,
          conversationRunning: false,
          loading: false,
          messages: [
            {
              id: 1,
              role: 'assistant',
              content: '',
              error: '已找到可验证的恢复入口，可以继续执行。',
              createdAt: 1
            }
          ],
          onOpenCodeChangeFile: () => undefined,
          onRevertCodeChanges: () => undefined,
          onSubmitClarification: async () => undefined,
          revertingCodeChangeIds: new Set()
        }),
        createElement(RecoverySurface, {
          activeExecutionRecovery: recovery('ready'),
          isApplicationPlanningPhase: false,
          onExecuteRecoveryAction: () => undefined,
          recoveryRunning: false
        })
      )
    )
  )
  assert.equal(countOccurrences(markup, '此次任务执行未能完成'), 0)
  assert.equal(countOccurrences(markup, '继续执行'), 1)
  assert.doesNotMatch(markup, /已找到可验证的恢复入口，可以继续执行。/)
  assert.doesNotMatch(markup, /请查看错误详情和相关执行记录后重试/)
})

test('connection error shows one Retry entry without inventing a Recovery action', () => {
  const connection = {
    ...initialConnectionState(true),
    status: 'unavailable' as const,
    lastError: '与后端连接中断，当前规划状态尚未确认。'
  }
  const markup = renderToStaticMarkup(
    createElement(ConnectionStatusBanner, { connection, onReconnect: () => undefined })
  )
  assert.match(markup, /Backend 暂时不可用/)
  assert.match(markup, /与后端连接中断，当前规划状态尚未确认/)
  assert.match(markup, /重试/)
  assert.doesNotMatch(markup, /recovery-incident/)
  assert.doesNotMatch(markup, /错误详情/)
  assert.doesNotMatch(markup, /重新执行技术规划/)
})

test('Planning needs_attention retains a retry entry for Backend re-resolution', () => {
  let actionCalls = 0
  const markup = renderToStaticMarkup(
    createElement(ApplicationPlanningRecoveryIncidentCard, {
      planning: {
        application: { id: 'app-A', appName: 'Demo App' },
        threadId: 'thread-A',
        connection: initialConnectionState(true),
        transportState: 'idle',
        error: '人工处理',
        lifecycle: {
          application: { id: 'app-A', name: 'Demo App' },
          revision: 1,
          updatedAt: '2026-09-12T00:00:00.000Z',
          initialization: { status: 'failed', stage: 'generating_technical_plan' },
          activeExecutions: {},
          extensions: {}
        },
    recovery: {
          schemaVersion: 'application-planning-recovery.v1',
          classification: 'blocked',
          sourceRunId: 'run-B',
          threadId: 'thread-A',
          canContinue: false,
          userActionRequired: false,
          inputCommitted: false,
          reasonCode: 'RECOVERY_BLOCKED',
          message: '无法安全恢复。',
          failureDiagnostic: {
            sourceRunId: 'run-B',
            origin: 'model_call',
            code: 'PLANNING_RECOVERY_DIAGNOSTIC',
            operation: 'technical_planning',
            message: 'Planning diagnostic visible'
          },
          recoveryActionPlan: {
            schemaVersion: 'recovery-action-plan.v1',
            incidentId: 'incident-B',
            sourceRunId: 'run-B',
            threadId: 'thread-A',
            executionKind: 'application_planning',
            status: 'needs_attention',
            reasonCode: 'RECOVERY_BLOCKED',
            message: '当前现场没有可证明安全的自动恢复入口，需要人工处理。',
            primaryAction: null,
            alternateActions: []
          }
        }
      } as ApplicationPlanningCurrentState,
      onAction: () => {
        actionCalls += 1
      }
    })
  )
  assert.doesNotMatch(markup, /RECOVERY_BLOCKED/)
  assert.match(markup, /Planning diagnostic visible/)
  assert.match(markup, /<button/)
  assert.match(markup, /重试/)
  assert.equal(actionCalls, 0)
})

test('caller keeps historical errors unchanged and renders one current Incident', () => {
  const historyErrors = ['404 model-A', '404 model-B']
  const planning = planningStateFromRecovery({
    sourceRunId: 'run-B',
    model: 'model-B',
    diagnosticMessage: 'Model-B not found'
  })
  const markup = renderPlanningRecoverySurface(historyErrors, planning)

  assert.equal(countOccurrences(markup, '>404 model-A<'), 1)
  assert.equal(countOccurrences(markup, '>404 model-B<'), 1)
  assert.equal(
    countOccurrences(markup, 'data-testid="application-planning-recovery-incident"'),
    1
  )
  assert.equal(countOccurrences(markup, '<span>重新执行技术规划</span>'), 1)
})

test('caller updates only the current Incident when the canonical source moves from B to C', () => {
  const historyErrors = ['404 model-A', '404 model-B']
  const firstMarkup = renderPlanningRecoverySurface(
    historyErrors,
    planningStateFromRecovery({
      sourceRunId: 'run-B',
      model: 'model-B',
      diagnosticMessage: 'Model-B not found'
    })
  )
  const secondMarkup = renderPlanningRecoverySurface(
    historyErrors,
    planningStateFromRecovery({
      sourceRunId: 'run-C',
      model: 'model-C',
      diagnosticMessage: 'Model-C unavailable'
    })
  )

  assert.match(firstMarkup, /Model-B not found/)
  assert.doesNotMatch(secondMarkup, /Model-B not found/)
  assert.match(secondMarkup, /Model-C unavailable/)
  assert.equal(countOccurrences(secondMarkup, '404 model-A'), 1)
  assert.equal(countOccurrences(secondMarkup, '404 model-B'), 1)
  assert.equal(
    countOccurrences(secondMarkup, 'data-testid="application-planning-recovery-incident"'),
    1
  )
})

test('caller projects needs_attention as the only actionable current Incident', () => {
  const markup = renderPlanningRecoverySurface(
    ['404 model-A', '404 model-B'],
    planningStateFromRecovery({
      sourceRunId: 'run-B',
      model: 'model-B',
      diagnosticMessage: 'Model-B unavailable',
      status: 'needs_attention'
    })
  )

  assert.equal(
    countOccurrences(markup, 'data-testid="application-planning-recovery-incident"'),
    1
  )
  assert.doesNotMatch(markup, /NATIVE_SUBGRAPH_REPLAY_UNSUPPORTED/)
  assert.match(markup, /当前现场没有可证明安全的自动恢复入口，需要人工处理/)
  assert.match(markup, /<button/)
  assert.match(markup, /重试/)
  assert.doesNotMatch(markup, /重新执行技术规划/)
})

test('Planning caller renders one Incident and suppresses both legacy candidate kinds', () => {
  const planning = planningStateFromRecovery({
    sourceRunId: 'run-planning',
    model: 'model-planning',
    diagnosticMessage: 'Planning model unavailable'
  })
  const markup = renderRecoverySurface({
    candidate: recovery('ready', 'application_planning'),
    isApplicationPlanningPhase: true,
    planning
  })

  assert.equal(countOccurrences(markup, 'data-testid="application-planning-recovery-incident"'), 1)
  assert.equal(countOccurrences(markup, 'data-testid="execution-recovery-card"'), 0)
})

test('Workbench caller renders one unified current Recovery Incident', () => {
  const markup = renderRecoverySurface({
    candidate: recovery('ready'),
    isApplicationPlanningPhase: false
  })

  assert.equal(countOccurrences(markup, 'data-testid="application-planning-recovery-incident"'), 0)
  assert.equal(countOccurrences(markup, 'data-testid="workbench-recovery-incident"'), 1)
})

test('J8 awaiting_user leaves the business confirmation card as the only control surface', () => {
  const markup = renderToStaticMarkup(
    createElement(ApplicationPlanningRecoveryIncidentCard, {
      planning: {
        application: { id: 'app-A', appName: 'Demo App' },
        threadId: 'thread-A',
        connection: initialConnectionState(true),
        transportState: 'idle',
        lifecycle: {
          application: { id: 'app-A', name: 'Demo App' },
          revision: 1,
          updatedAt: '2026-09-12T00:00:00.000Z',
          initialization: { status: 'awaiting_user', stage: 'awaiting_technical_plan_confirmation' },
          activeExecutions: {},
          extensions: {}
        },
        recovery: {
          schemaVersion: 'application-planning-recovery.v1',
          classification: 'awaiting_user',
          sourceRunId: 'run-B',
          threadId: 'thread-A',
          canContinue: false,
          userActionRequired: true,
          inputCommitted: false,
          reasonCode: 'NATIVE_APPLICATION_PLANNING_INTERRUPT',
          message: '当前应用规划正在等待你的确认。',
          recoveryActionPlan: null
        }
      } as ApplicationPlanningCurrentState
    })
  )
  assert.equal(markup, '')
})

// 验证真实规划页面入口复用同一 Current Incident，而不是历史错误卡。
test('application planning modal renders the shared current incident', () => {
  const planning = {
    application: { id: 'app-A', appName: 'Demo App' },
    threadId: 'thread-A',
    connection: initialConnectionState(true),
    transportState: 'idle',
    error: 'Service Unavailable',
    lifecycle: {
      application: { id: 'app-A', name: 'Demo App' },
      revision: 1,
      updatedAt: '2026-09-12T00:00:00.000Z',
      initialization: { status: 'failed', stage: 'generating_technical_plan' },
      activeExecutions: {},
      extensions: {}
    },
      recovery: {
      schemaVersion: 'application-planning-recovery.v1',
      classification: 'ready_to_continue',
      sourceRunId: 'run-B',
      threadId: 'thread-A',
      canContinue: true,
      userActionRequired: false,
      inputCommitted: true,
      reasonCode: 'APPLICATION_PLANNING_MODEL_GENERATION_REPLAY_SAFE',
      message: '当前执行现场可以安全继续。',
      recoveryActionPlan: {
          schemaVersion: 'recovery-action-plan.v1',
          incidentId: 'incident-modal',
          sourceRunId: 'run-B',
          threadId: 'thread-A',
          executionKind: 'application_planning',
          status: 'recoverable',
          reasonCode: 'APPLICATION_PLANNING_MODEL_GENERATION_REPLAY_SAFE',
          message: '当前执行现场可以安全继续。',
          primaryAction: {
            actionId: 'action-modal',
            kind: 'continue_checkpoint',
            label: '继续执行',
            description: '从 checkpoint 继续。',
            requiresConfirmation: false
          },
          alternateActions: []
      },
      failureDiagnostic: {
        sourceRunId: 'run-B',
        origin: 'model_call',
        code: 'http_503',
        httpStatus: 503,
        model: 'mimo-v2.5-pro',
        message: 'Service Unavailable'
      }
    }
  } as ApplicationPlanningCurrentState
  const markup = renderToStaticMarkup(
    createElement(ApplicationPagePlanningModal, {
      planning,
      streamingContent: '',
      generatingTemplate: false,
      theme: 'light',
      visible: true,
      onReturnHome: () => undefined,
      onSubmit: async () => undefined,
      onSaveRequirementSpec: async () => ({ artifact: {} as never, requirementSpec: {} }),
      onRetry: () => undefined
    })
  )
  assert.match(markup, /HTTP 状态码[\s\S]*503/)
  assert.match(markup, /模型[\s\S]*mimo-v2\.5-pro/)
  assert.match(markup, /Service Unavailable/)
  assert.match(markup, /当前执行现场可以安全继续/)
  assert.match(markup, /继续执行/)
  assert.match(markup, /application-planning-recovery-incident/)
})
