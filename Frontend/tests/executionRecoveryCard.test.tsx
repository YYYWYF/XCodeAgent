import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement, Fragment } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { WorkbenchPhaseContext } from '../src/renderer/src/context'
import RecoveryIncidentCard from '../src/renderer/src/components/RecoveryIncidentCard/RecoveryIncidentCard'
import ApplicationPagePlanningModal from '../src/renderer/src/components/Welcome/ApplicationPagePlanningModal'
import AgentErrorCard from '../src/renderer/src/components/AgentErrorCard'
import ApplicationPlanningRecoveryIncidentCard from '../src/renderer/src/components/ApplicationPlanningRecoveryIncidentCard'
import MessageList from '../src/renderer/src/components/AiChatPanel/components/MessageList'
import RecoverySurface from '../src/renderer/src/components/AiChatPanel/recoverySurface'
import { WORKBENCH_PHASE_AGENTS } from '../src/renderer/src/workbenchPhase'
import { applicationPlanningRecoveryProjection } from '../src/renderer/src/service/applicationPlanningRecovery'
import type { ApplicationPlanningCurrentState } from '../src/renderer/src/service/activeApplicationPlanning'
import { workbenchRecoveryIncident } from '../src/renderer/src/service/recoveryIncident'
import type { ExecutionRecoveryCandidate, WorkflowRunPayload } from '../src/renderer/src/typings'

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
        createElement(AgentErrorCard, { error, historical: true, key: `history-${index}` })
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

test('needs_attention Recovery Incident keeps a permanent retry entry', () => {
  for (const availability of ['blocked', 'requires_handler'] as const) {
    const incident = workbenchRecoveryIncident(recovery(availability))
    if (!incident) throw new Error('测试候选未生成 Workbench Incident。')
    const markup = renderToStaticMarkup(
      createElement(RecoveryIncidentCard, {
        incident,
        onAction: () => undefined
      })
    )
    assert.match(markup, /重试/)
    assert.doesNotMatch(markup, /继续执行/)
  }
})

test('planning Recovery Incident shows safe failure summary and Backend action', () => {
  const markup = renderToStaticMarkup(
    createElement(ApplicationPlanningRecoveryIncidentCard, {
      planning: {
        application: { id: 'app-A', appName: 'Demo App' },
        threadId: 'thread-A',
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
  assert.match(markup, /404 · mimo-v2\.5-pro/)
  assert.match(markup, /Model not found/)
  assert.match(markup, /错误详情/)
  assert.match(markup, /重新执行技术规划/)
  assert.match(markup, /已验证正式规划输入，可以重新执行技术规划/)
})

test('ordinary AgentErrorCard remains a history-only error card', () => {
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

test('historical AgentErrorCard omits recovery guidance and retry action', () => {
  const markup = renderToStaticMarkup(
    createElement(AgentErrorCard, {
      error: '普通任务失败',
      historical: true,
      onRetry: () => undefined
    })
  )
  assert.match(markup, /普通任务失败/)
  assert.doesNotMatch(markup, /请查看错误详情和相关执行记录后重试/)
  assert.doesNotMatch(markup, />重试</)
})

test('MessageList renders legacy recovery guidance as history while keeping Current Recovery Incident unique', () => {
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
  assert.equal(countOccurrences(markup, '此次任务执行未能完成'), 1)
  assert.equal(countOccurrences(markup, '继续执行'), 1)
  assert.doesNotMatch(markup, /已找到可验证的恢复入口，可以继续执行。/)
  assert.doesNotMatch(markup, /请查看错误详情和相关执行记录后重试/)
})

test('sync error Incident stays outside history and offers only resync', () => {
  const markup = renderToStaticMarkup(
    createElement(ApplicationPlanningRecoveryIncidentCard, {
      planning: {
        application: { id: 'app-A', appName: 'Demo App' },
        threadId: 'thread-A',
        transportState: 'reconciling',
        syncError: '与后端连接中断，当前规划状态尚未确认。',
        lifecycle: {
          application: { id: 'app-A', name: 'Demo App' },
          revision: 1,
          updatedAt: '2026-09-12T00:00:00.000Z',
          initialization: { status: 'running', stage: 'generating_technical_plan' },
          activeExecutions: {},
          extensions: {}
        }
      } as ApplicationPlanningCurrentState,
      onAction: () => undefined
    })
  )
  assert.match(markup, /规划状态尚未同步/)
  assert.match(markup, /与后端连接中断，当前规划状态尚未确认/)
  assert.match(markup, /重新同步状态/)
  assert.doesNotMatch(markup, /错误详情/)
  assert.doesNotMatch(markup, /重新执行技术规划/)
})

test('needs_attention Incident shows reason and a permanent retry entry', () => {
  const markup = renderToStaticMarkup(
    createElement(ApplicationPlanningRecoveryIncidentCard, {
      planning: {
        application: { id: 'app-A', appName: 'Demo App' },
        threadId: 'thread-A',
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
      onAction: () => undefined
    })
  )
  assert.match(markup, /RECOVERY_BLOCKED/)
  assert.match(markup, /重试/)
})

test('caller keeps historical errors unchanged and renders one current Incident', () => {
  const historyErrors = ['404 model-A', '404 model-B']
  const planning = planningStateFromRecovery({
    sourceRunId: 'run-B',
    model: 'model-B',
    diagnosticMessage: 'Model-B not found'
  })
  const markup = renderPlanningRecoverySurface(historyErrors, planning)

  assert.equal(countOccurrences(markup, '404 model-A'), 1)
  assert.equal(countOccurrences(markup, '404 model-B'), 1)
  assert.equal(
    countOccurrences(markup, 'data-testid="application-planning-recovery-incident"'),
    1
  )
  assert.equal(countOccurrences(markup, '重新执行技术规划'), 1)
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

test('caller projects needs_attention as the only current Incident with retry entry', () => {
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
  assert.match(markup, /NATIVE_SUBGRAPH_REPLAY_UNSUPPORTED/)
  assert.match(markup, /当前现场没有可证明安全的自动恢复入口，需要人工处理/)
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

test('awaiting_user leaves the business confirmation card as the only control surface', () => {
  const markup = renderToStaticMarkup(
    createElement(ApplicationPlanningRecoveryIncidentCard, {
      planning: {
        application: { id: 'app-A', appName: 'Demo App' },
        threadId: 'thread-A',
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
  assert.match(markup, /503 · mimo-v2\.5-pro/)
  assert.match(markup, /Service Unavailable/)
  assert.match(markup, /当前执行现场可以安全继续/)
  assert.match(markup, /继续执行/)
  assert.match(markup, /application-planning-recovery-incident/)
})
