import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ExecutionRecoveryCard from '../src/renderer/src/components/AiChatPanel/components/ExecutionRecoveryCard'
import ApplicationPagePlanningModal from '../src/renderer/src/components/Welcome/ApplicationPagePlanningModal'
import AgentErrorCard from '../src/renderer/src/components/AgentErrorCard'
import ApplicationPlanningRecoveryIncidentCard from '../src/renderer/src/components/ApplicationPlanningRecoveryIncidentCard'
import type { ApplicationPlanningCurrentState } from '../src/renderer/src/service/activeApplicationPlanning'
import type { ExecutionRecoveryCandidate } from '../src/renderer/src/typings'

/** 构造恢复卡片所需的公开候选。 */
function recovery(
  availability: ExecutionRecoveryCandidate['availability']
): ExecutionRecoveryCandidate {
  return {
    sourceRunId: 'run-A',
    ownerSessionId: 'session-A',
    threadId: 'thread-A',
    executionKind: 'workbench',
    executionStatus: 'interrupted',
    availability,
    canContinue: availability === 'ready',
    reasonCode: 'RECOVERY_TEST',
    message: '恢复测试',
    updatedAt: '2026-09-12T00:00:00.000Z'
  }
}

test('READY recovery card exposes continue action', () => {
  const markup = renderToStaticMarkup(
    createElement(ExecutionRecoveryCard, {
      recovery: recovery('ready'),
      loading: false,
      onContinue: () => undefined
    })
  )
  assert.match(markup, /上一次执行未正常完成/)
  assert.match(markup, /继续执行/)
})

test('blocked and requires-handler recovery cards hide continue action', () => {
  for (const availability of ['blocked', 'requires_handler'] as const) {
    const markup = renderToStaticMarkup(
      createElement(ExecutionRecoveryCard, {
        recovery: recovery(availability),
        loading: false,
        onContinue: () => undefined
      })
    )
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

test('needs_attention Incident shows reason without an action button', () => {
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
      } as ApplicationPlanningCurrentState
    })
  )
  assert.match(markup, /RECOVERY_BLOCKED/)
  assert.doesNotMatch(markup, /<button/)
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
