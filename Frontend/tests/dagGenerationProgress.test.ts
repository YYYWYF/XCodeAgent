import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  buildWorkflowForwardedProps,
  newerDagGenerationSnapshot,
  readDagGenerationSnapshot
} from '../src/renderer/src/service/agUiAgent'
import type {
  DagGenerationSnapshot,
  DagGenerationUnitRecord
} from '../src/renderer/src/service/agUiAgent'
import { processStepsForDisplay } from '../src/renderer/src/service/processStepHistory'
import {
  dagGenerationStrategyLabel,
  dagGenerationSummaryCopy,
  dagGenerationUnitAttemptCopy,
  dagGenerationUnitStatusLabel,
  dagGenerationUnitTaskCopy,
  currentDagConfirmationDraftIdentity,
  bindDagConfirmationDraftIdentity,
  latestDagGenerationSnapshot,
  pendingDagConfirmationExecution,
  pendingDagConfirmationWorkflow,
  pendingDagOwnerSessionId,
  planningRefreshInterruption,
  resolvePendingPlanGuard
} from '../src/renderer/src/components/AiChatPanel/stageOutputState'
import {
  workflowInteractionAvailability,
  workflowMessageInteractionAvailability
} from '../src/renderer/src/components/AiChatPanel/planExecutionMode'
import { maybeRefreshPendingPlanLifecycleAfterGeneration } from '../src/renderer/src/components/AiChatPanel/pendingPlanLifecycleRefresh'
import { latestApplicationLifecycle } from '../src/renderer/src/hooks/useApplicationLifecycleStore'
import type { AgentChatMessage } from '../src/renderer/src/components/AiChatPanel/types'
import type {
  ApplicationLifecycle,
  WorkbenchExecution,
  WorkflowRunPayload
} from '../src/renderer/src/typings'

const DAG_DRAFT_DIGEST = 'f'.repeat(64)

/** 构造一个完整 Unit，测试只覆盖被覆写的进度事实。 */
function unit(overrides: Partial<DagGenerationUnitRecord> = {}): DagGenerationUnitRecord {
  return {
    id: 'page:orders',
    kind: 'page',
    participation: 'generate_only',
    generationStrategy: 'model',
    status: 'pending',
    generationRound: 1,
    attemptInRound: 0,
    localAttemptLimit: 3,
    totalAttempts: 0,
    retainedTaskCount: 0,
    reusableCapabilityCount: 0,
    candidateTaskCount: 0,
    issues: [],
    ...overrides
  }
}

/** 构造新版 PlanningRun Snapshot，确保测试覆盖当前唯一协议。 */
function snapshot(
  overrides: Partial<DagGenerationSnapshot> = {},
  units: DagGenerationUnitRecord[] = [unit()]
): DagGenerationSnapshot {
  return {
    schemaVersion: 'dag-generation.v1',
    planningRunId: 'planning-run-1',
    revision: 1,
    status: 'active',
    phase: 'generating_units',
    globalRepairRound: 0,
    globalRepairLimit: 2,
    units,
    globalIssues: [],
    summary: {
      unitCount: units.length,
      readyUnitCount: units.filter((item) =>
        ['not_required', 'candidate_ready'].includes(item.status)
      ).length,
      pendingUnitCount: units.filter((item) => item.status === 'pending').length,
      activeUnitCount: units.filter((item) => ['generating', 'validating'].includes(item.status))
        .length,
      roundExhaustedUnitCount: units.filter((item) => item.status === 'round_exhausted').length,
      abortedUnitCount: units.filter((item) => item.status === 'aborted').length,
      retainedTaskCount: units.reduce((count, item) => count + item.retainedTaskCount, 0),
      candidateTaskCount: units.reduce((count, item) => count + item.candidateTaskCount, 0),
      unitIssueCount: units.reduce((count, item) => count + item.issues.length, 0),
      globalIssueCount: 0,
      failureIssueCount: 0
    },
    ...overrides
  }
}

/** 构造带稳定 DraftIdentity 的 Build DAG 待确认 execution。 */
function pendingDagExecution(): WorkbenchExecution {
  return {
    scope: 'application',
    targetId: 'application',
    threadId: 'thread-dag-transition',
    runId: 'workflow-dag-transition',
    phase: 'prepare_build_tasks',
    status: 'awaiting_user',
    pendingInteraction: {
      id: 'interaction-current',
      type: 'task_plan_confirmation',
      basedOnRevision: 7,
      payload: {
        mode: 'build_task_plan_confirmation',
        draftIdentity: {
          ownerSessionId: 'session-dag-transition',
          planningRunId: 'planning-dag-transition',
          draftDigest: DAG_DRAFT_DIGEST
        }
      },
      artifactRefs: [],
      createdAt: '2026-09-10T00:00:01Z',
      submittedAt: null
    },
    startedAt: '2026-09-10T00:00:00Z',
    updatedAt: '2026-09-10T00:00:01Z'
  }
}

/** 构造 interaction 帧尚未同步、但 DraftIdentity 与当前 Pending 一致的确认 Workflow。 */
function dagConfirmationWorkflow(lifecycle: ApplicationLifecycle): WorkflowRunPayload {
  const execution = pendingDagExecution()
  const snapshotExecution = {
    ...execution,
    pendingInteraction: execution.pendingInteraction
      ? {
          ...execution.pendingInteraction,
          id: 'interaction-workflow-frame',
          basedOnRevision: 6
        }
      : undefined
  }
  const snapshotLifecycle = {
    ...lifecycle,
    revision: 6,
    activeExecutions: { [snapshotExecution.runId]: snapshotExecution }
  }
  const clarification = {
    mode: 'build_task_plan_confirmation',
    status: 'requires_user_input',
    draftIdentity: {
      ownerSessionId: 'session-dag-transition',
      planningRunId: 'planning-dag-transition',
      draftDigest: DAG_DRAFT_DIGEST
    },
    taskPlan: { confirmationStatus: 'pending', scopeTasks: [] }
  }
  return {
    runId: execution.runId,
    threadId: execution.threadId,
    events: [],
    summary: {
      status: 'requires_user_input',
      phase: 'prepare_build_tasks',
      clarification,
      lifecycle: snapshotLifecycle
    },
    state: { clarification, lifecycle: snapshotLifecycle },
    result: { clarification, lifecycle: snapshotLifecycle }
  } as unknown as WorkflowRunPayload
}

/** 构造 DAG 已确认后进入 Unit Test 的交互，保留历史 DAG 投影以覆盖回归。 */
function unitTestConfirmationTransition(): {
  workflow: WorkflowRunPayload
  lifecycle: ApplicationLifecycle
} {
  const execution: WorkbenchExecution = {
    scope: 'application',
    targetId: 'application',
    threadId: 'thread-unit-test-transition',
    runId: 'workflow-unit-test-transition',
    phase: 'unit_test',
    status: 'awaiting_user',
    pendingInteraction: {
      id: 'interaction-unit-test',
      type: 'unit_test_confirmation',
      basedOnRevision: 12,
      payload: { mode: 'unit_test_confirmation' },
      artifactRefs: [],
      createdAt: '2026-09-11T00:00:00Z',
      submittedAt: null
    },
    startedAt: '2026-09-11T00:00:00Z',
    updatedAt: '2026-09-11T00:00:01Z'
  }
  const lifecycle = {
    application: { id: 'app-unit-test-transition', name: 'App' },
    updatedAt: '2026-09-11T00:00:01Z',
    revision: 12,
    initialization: { stage: 'ready_for_workbench', status: 'completed' },
    activeExecutions: { [execution.runId]: execution },
    extensions: {
      planningRefresh: {
        schemaVersion: 'planning-refresh.v1',
        source: 'confirmed_plan',
        status: 'confirmed',
        planningRunId: 'planning-dag-transition',
        workflowRunId: 'workflow-dag-transition',
        draftDigest: DAG_DRAFT_DIGEST,
        confirmedPlanDigest: DAG_DRAFT_DIGEST,
        message: 'DAG 已确认并完成执行计划。'
      }
    }
  } as unknown as ApplicationLifecycle
  const clarification = {
    mode: 'unit_test_confirmation',
    status: 'requires_user_input',
    message: '单元测试已完成，请确认是否继续。'
  }
  const historicalDagConfirmation = {
    mode: 'build_task_plan_confirmation',
    status: 'completed',
    taskPlan: { confirmationStatus: 'confirmed', scopeTasks: [] }
  }
  return {
    lifecycle,
    workflow: {
      runId: execution.runId,
      threadId: execution.threadId,
      events: [],
      summary: {
        status: 'requires_user_input',
        phase: 'unit_test',
        clarification,
        buildTaskPlanConfirmation: historicalDagConfirmation,
        lifecycle
      },
      state: { clarification, lifecycle },
      result: { clarification, lifecycle }
    } as unknown as WorkflowRunPayload
  }
}

test('DAG Confirm 后进入 Unit Test 时，当前 clarification 优先于历史 DAG 投影', () => {
  const { workflow, lifecycle } = unitTestConfirmationTransition()

  assert.equal(workflowInteractionAvailability(workflow, lifecycle), 'active')
  assert.equal(workflowMessageInteractionAvailability(workflow, lifecycle, false, false), 'active')
})

test('当前 clarification 仍是 DAG confirmation 时继续遵守 terminal stale 防护', () => {
  const execution = pendingDagExecution()
  const lifecycle = {
    application: { id: 'app-dag-terminal', name: 'App' },
    updatedAt: '2026-09-11T00:00:01Z',
    revision: 8,
    initialization: { stage: 'ready_for_workbench', status: 'completed' },
    activeExecutions: { [execution.runId]: execution },
    extensions: {
      planningRefresh: {
        schemaVersion: 'planning-refresh.v1',
        source: 'confirmed_plan',
        status: 'confirmed',
        planningRunId: 'planning-dag-transition',
        workflowRunId: execution.runId,
        draftDigest: DAG_DRAFT_DIGEST,
        message: 'DAG 已确认。'
      }
    }
  } as unknown as ApplicationLifecycle
  const workflow = dagConfirmationWorkflow(lifecycle)

  assert.equal(workflowInteractionAvailability(workflow, lifecycle), 'stale')
  assert.equal(workflowMessageInteractionAvailability(workflow, lifecycle, false, false), 'stale')
})

test('当前 clarification 缺失时，PendingPlan recovery 仍使用历史 DAG confirmation fallback', () => {
  const confirmation = {
    mode: 'build_task_plan_confirmation',
    status: 'requires_user_input',
    draftIdentity: {
      ownerSessionId: 'session-recovered',
      planningRunId: 'planning-recovered',
      draftDigest: 'a'.repeat(64)
    },
    taskPlan: { confirmationStatus: 'pending', scopeTasks: [] }
  }
  const lifecycle = {
    application: { id: 'app-recovered', name: 'App' },
    updatedAt: '2026-09-11T00:00:02Z',
    revision: 12,
    initialization: { stage: 'ready_for_workbench', status: 'completed' },
    activeExecutions: {},
    extensions: {
      planningRefresh: {
        schemaVersion: 'planning-refresh.v1',
        source: 'pending_plan',
        status: 'awaiting_confirmation',
        planningRunId: 'planning-recovered',
        workflowRunId: 'workflow-recovered',
        ownerSessionId: 'session-recovered',
        draftDigest: 'a'.repeat(64),
        confirmation,
        message: '已从 PendingPlan 恢复待确认任务规划。'
      }
    }
  } as unknown as ApplicationLifecycle
  const workflow = {
    runId: 'workflow-recovered',
    threadId: 'thread-recovered',
    events: [],
    summary: {
      status: 'requires_user_input',
      phase: 'prepare_build_tasks',
      buildTaskPlanConfirmation: confirmation,
      lifecycle
    },
    state: { lifecycle },
    result: { lifecycle }
  } as unknown as WorkflowRunPayload

  assert.equal(workflowInteractionAvailability(workflow, lifecycle), 'active')
  assert.equal(workflowMessageInteractionAvailability(workflow, lifecycle, false, false), 'active')
})

test('新版 Snapshot 会严格解析，并丢弃 Candidate 正文和旧 stages 协议', () => {
  const issueWithPrivateTaskId = {
    code: 'unit.contract',
    level: 'unit',
    category: 'contract',
    unitIds: ['page:orders'],
    taskIds: ['candidate:secret'],
    retryUnitIds: ['page:orders'],
    retryable: true,
    message: '契约需要修复'
  }
  const parsed = readDagGenerationSnapshot({
    ...snapshot({}, [
      unit({
        participation: 'reuse_and_generate',
        status: 'candidate_ready',
        retainedTaskCount: 2,
        candidateTaskCount: 1,
        issues: [issueWithPrivateTaskId]
      })
    ]),
    tasks: [{ id: 'candidate:secret', body: 'candidate-body-must-stay-private' }],
    candidateBody: 'candidate-body-must-stay-private'
  })

  assert.ok(parsed)
  assert.equal(parsed.schemaVersion, 'dag-generation.v1')
  assert.equal(parsed.planningRunId, 'planning-run-1')
  assert.equal(parsed.revision, 1)
  assert.equal(parsed.units[0]?.participation, 'reuse_and_generate')
  assert.equal(parsed.units[0]?.issues[0]?.message, '契约需要修复')
  assert.equal('tasks' in parsed, false)
  assert.equal(JSON.stringify(parsed).includes('candidate-body-must-stay-private'), false)
  assert.equal(JSON.stringify(parsed).includes('candidate:secret'), false)
  assert.equal(readDagGenerationSnapshot({ stages: [] }), undefined)
})

test('任一 Unit 的未知生命周期枚举或空 id 会拒绝整份 Snapshot', () => {
  const invalidUnits = [
    { ...unit(), participation: 'some_future_participation' },
    { ...unit(), generationStrategy: 'some_future_strategy' },
    { ...unit(), status: 'some_future_status' },
    { ...unit(), id: '' }
  ]

  for (const invalidUnit of invalidUnits) {
    assert.equal(
      readDagGenerationSnapshot({
        ...snapshot(),
        units: [unit({ id: 'page:valid' }), invalidUnit]
      }),
      undefined
    )
  }
})

test('Unit 协议校验覆盖展示上限之外的条目，合法列表仍只展示前 200 条', () => {
  const units = Array.from({ length: 201 }, (_, index) => unit({ id: `page:${index}` }))
  const parsed = readDagGenerationSnapshot(snapshot({}, units))

  assert.ok(parsed)
  assert.equal(parsed.units.length, 200)
  assert.equal(parsed.summary.unitCount, 201)
  assert.equal(
    readDagGenerationSnapshot({
      ...snapshot({}, units),
      units: [...units.slice(0, 200), { ...unit({ id: 'page:200' }), status: 'some_future_status' }]
    }),
    undefined
  )
})

test('所有 Unit 状态都有稳定展示，waiting 与 round_exhausted 不伪装为百分比或 Run failed', () => {
  const statuses = [
    'not_required',
    'pending',
    'generating',
    'validating',
    'candidate_ready',
    'round_exhausted',
    'aborted'
  ] as const
  const labels = statuses.map((status) => dagGenerationUnitStatusLabel(unit({ status })))

  assert.deepEqual(labels, [
    'not_required',
    'waiting',
    'generating',
    'validating',
    'candidate_ready',
    'round_exhausted',
    'aborted'
  ])
  const exhausted = snapshot({}, [unit({ status: 'round_exhausted' })])
  assert.equal(dagGenerationSummaryCopy(exhausted), '本轮已耗尽，等待修复决策')
  assert.equal(dagGenerationSummaryCopy(exhausted).includes('failed'), false)
  assert.equal(JSON.stringify(exhausted).toLowerCase().includes('percent'), false)
})

test('模型重试与 reuse_and_generate 显示真实 attempt 和安全任务计数', () => {
  const retrying = unit({
    participation: 'reuse_and_generate',
    status: 'generating',
    attemptInRound: 2,
    totalAttempts: 2,
    retainedTaskCount: 2,
    candidateTaskCount: 1
  })

  assert.equal(dagGenerationUnitAttemptCopy(retrying), 'attempt 2/3')
  assert.equal(dagGenerationUnitTaskCopy(retrying), 'retained 2 / candidate 1')
  assert.equal(dagGenerationStrategyLabel(retrying), 'reuse_and_generate')
})

test('Global repair 显示真实轮次，failed/cancelled 只由 Run status 决定', () => {
  const repairing = snapshot({ globalRepairRound: 1, globalRepairLimit: 2 })
  assert.equal(dagGenerationSummaryCopy(repairing), '正在执行 Global repair 1/2')
  assert.equal(dagGenerationSummaryCopy(snapshot({ status: 'failed' })), 'PlanningRun failed')
  assert.equal(dagGenerationSummaryCopy(snapshot({ status: 'cancelled' })), 'PlanningRun cancelled')
})

test('shell 显示 prerequisite/reused，auth 确定性生成不展示虚假 attempt', () => {
  const shell = unit({
    id: 'frontend:shell',
    participation: 'prerequisite_only',
    generationStrategy: 'prerequisite_only',
    status: 'not_required',
    localAttemptLimit: 0
  })
  const reusedShell = unit({
    id: 'frontend:shell',
    participation: 'reuse_only',
    generationStrategy: 'reuse_only',
    status: 'not_required',
    localAttemptLimit: 0
  })
  const authorization = unit({
    id: 'authorization:bootstrap',
    kind: 'authorization',
    generationStrategy: 'deterministic',
    status: 'candidate_ready',
    attemptInRound: 0,
    localAttemptLimit: 0
  })

  assert.equal(dagGenerationUnitStatusLabel(shell), 'prerequisite')
  assert.equal(dagGenerationUnitStatusLabel(reusedShell), 'reused')
  assert.equal(dagGenerationStrategyLabel(authorization), 'deterministic')
  assert.equal(dagGenerationUnitAttemptCopy(authorization), '')
})

test('同一 PlanningRun 的旧 revision 不会覆盖新状态', () => {
  const newer = snapshot({ revision: 9 }, [
    unit({ status: 'validating', attemptInRound: 2, totalAttempts: 2 })
  ])
  const older = snapshot({ revision: 8 }, [unit({ status: 'generating', attemptInRound: 1 })])

  assert.equal(newerDagGenerationSnapshot(newer, older)?.revision, 9)
  assert.equal(newerDagGenerationSnapshot(older, newer)?.revision, 9)

  const messages = [
    {
      id: 1,
      role: 'assistant',
      content: '',
      createdAt: 1,
      processSteps: [
        {
          id: 'workflow:prepare_build_tasks',
          kind: 'workflow',
          status: 'running',
          title: '生成',
          detail: '',
          sequence: 1,
          dagGeneration: newer
        }
      ]
    },
    {
      id: 2,
      role: 'assistant',
      content: '',
      createdAt: 2,
      processSteps: [
        {
          id: 'workflow:prepare_build_tasks',
          kind: 'workflow',
          status: 'running',
          title: '生成',
          detail: '',
          sequence: 1,
          dagGeneration: older
        }
      ]
    }
  ] as AgentChatMessage[]
  assert.equal(latestDagGenerationSnapshot(messages)?.revision, 9)
})

test('完成事件携带旧 revision 时不覆盖实时步骤的新快照', () => {
  const current = snapshot({ revision: 6 }, [unit({ status: 'candidate_ready' })])
  const recovered = snapshot({ revision: 5 }, [unit({ status: 'validating' })])
  const steps = processStepsForDisplay(
    [
      {
        id: 'workflow:prepare_build_tasks',
        kind: 'workflow',
        status: 'completed',
        title: '已完成 构建任务 DAG 生成',
        detail: '',
        sequence: 1,
        nodeName: 'prepare_build_tasks',
        dagGeneration: current
      }
    ],
    {
      runId: 'run-revision',
      threadId: 'thread-revision',
      summary: { status: 'failed' },
      events: [
        {
          type: 'workflow.node.completed',
          nodeName: 'prepare_build_tasks',
          node: { label: '构建任务 DAG 生成' },
          status: 'completed',
          data: { detail: { dagGeneration: recovered } }
        }
      ]
    } as WorkflowRunPayload
  )

  assert.equal(steps?.[0]?.dagGeneration?.revision, 6)
  assert.equal(steps?.[0]?.dagGeneration?.units[0]?.status, 'candidate_ready')
})

test('待确认 DAG 由持久化 lifecycle 锁定且只匹配原 run 和 thread 的会话', () => {
  const execution = {
    scope: 'page',
    targetId: 'home',
    pageId: 'home',
    threadId: 'thread-dag',
    runId: 'run-dag',
    phase: 'prepare_build_tasks',
    status: 'awaiting_user',
    pendingInteraction: {
      id: 'interaction-dag',
      type: 'plan_adjustment',
      basedOnRevision: 8,
      payload: { mode: 'build_task_plan_confirmation' },
      artifactRefs: [],
      createdAt: '2026-09-03T00:00:00Z'
    },
    startedAt: '2026-09-03T00:00:00Z',
    updatedAt: '2026-09-03T00:00:00Z'
  } as const
  const lifecycle = {
    activeExecutions: { 'run-dag': execution }
  } as unknown as ApplicationLifecycle
  const workflow = {
    runId: 'run-dag',
    threadId: 'thread-dag',
    events: [],
    summary: {
      status: 'requires_user_input',
      clarification: {
        mode: 'build_task_plan_confirmation',
        taskPlan: { scopeTasks: [] }
      }
    }
  } as unknown as WorkflowRunPayload

  assert.equal(pendingDagConfirmationExecution(lifecycle)?.runId, 'run-dag')
  assert.equal(
    pendingDagConfirmationWorkflow(
      [{ id: 1, role: 'assistant', content: '', createdAt: 1, workflow }],
      execution
    ),
    workflow
  )
  assert.equal(
    pendingDagConfirmationWorkflow(
      [
        {
          id: 2,
          role: 'assistant',
          content: '',
          createdAt: 2,
          workflow: { ...workflow, threadId: 'other-thread' }
        }
      ],
      execution
    ),
    undefined
  )
})

test('browser refresh 优先采用 Backend 当前 active PlanningRun 而非旧聊天 revision', () => {
  const recovered = snapshot({ revision: 12 }, [
    unit({ status: 'validating', attemptInRound: 2, totalAttempts: 2 })
  ])
  const historical = snapshot({ revision: 3 }, [
    unit({ status: 'generating', attemptInRound: 1, totalAttempts: 1 })
  ])
  const lifecycle = {
    extensions: {
      planningRefresh: {
        schemaVersion: 'planning-refresh.v1',
        source: 'active_planning_run',
        status: 'planning',
        planningRunId: recovered.planningRunId,
        workflowRunId: 'workflow-refresh',
        threadId: 'thread-refresh',
        dagGeneration: recovered,
        message: '已恢复当前 PlanningRun 进度。'
      }
    }
  } as unknown as ApplicationLifecycle
  const messages = [
    {
      id: 1,
      role: 'assistant',
      content: '',
      createdAt: 1,
      processSteps: [
        {
          id: 'workflow:prepare_build_tasks',
          kind: 'workflow',
          status: 'running',
          title: '生成',
          detail: '',
          sequence: 1,
          dagGeneration: historical
        }
      ]
    }
  ] as AgentChatMessage[]

  assert.equal(latestDagGenerationSnapshot(messages, lifecycle)?.revision, 12)
  assert.equal(latestDagGenerationSnapshot(messages, lifecycle)?.units[0]?.status, 'validating')
})

test('refresh Pending 使用 Backend 确认投影，stale chat message 不能覆盖', () => {
  const execution = {
    scope: 'page',
    targetId: 'orders',
    pageId: 'orders',
    threadId: 'thread-current',
    runId: 'workflow-current',
    phase: 'prepare_build_tasks',
    status: 'awaiting_user',
    pendingInteraction: {
      id: 'interaction-current',
      type: 'task_plan_confirmation',
      basedOnRevision: 9,
      payload: { mode: 'build_task_plan_confirmation' },
      artifactRefs: [],
      createdAt: '2026-09-08T00:00:00Z'
    },
    startedAt: '2026-09-08T00:00:00Z',
    updatedAt: '2026-09-08T00:00:00Z'
  } as const
  const lifecycle = {
    revision: 9,
    updatedAt: '2026-09-08T00:00:00Z',
    activeExecutions: { 'workflow-current': execution },
    extensions: {
      planningRefresh: {
        schemaVersion: 'planning-refresh.v1',
        source: 'pending_plan',
        status: 'awaiting_confirmation',
        planningRunId: 'planning-current',
        workflowRunId: 'workflow-current',
        ownerSessionId: 'session-current',
        draftDigest: 'd'.repeat(64),
        buildExecutionScope: { type: 'page', targetId: 'orders' },
        confirmation: {
          mode: 'build_task_plan_confirmation',
          status: 'requires_user_input',
          draftIdentity: {
            ownerSessionId: 'session-current',
            planningRunId: 'planning-current',
            draftDigest: 'd'.repeat(64)
          },
          taskPlan: {
            confirmationStatus: 'pending',
            scopeTasks: [{ id: 'current-task', title: '当前任务', description: '' }]
          }
        },
        message: '已从 PendingPlan 恢复待确认任务规划。'
      }
    }
  } as unknown as ApplicationLifecycle
  const staleWorkflow = {
    runId: 'workflow-current',
    threadId: 'thread-current',
    events: [],
    summary: {
      status: 'requires_user_input',
      clarification: {
        mode: 'build_task_plan_confirmation',
        taskPlan: {
          confirmationStatus: 'pending',
          scopeTasks: [{ id: 'stale-task', title: '旧任务', description: '' }]
        }
      }
    }
  } as unknown as WorkflowRunPayload
  const messages = [
    { id: 1, role: 'assistant', content: '', createdAt: 1, workflow: staleWorkflow }
  ] as AgentChatMessage[]

  const restoredExecution = pendingDagConfirmationExecution(lifecycle)
  const restoredWorkflow = pendingDagConfirmationWorkflow(messages, restoredExecution, lifecycle)

  assert.equal(restoredExecution?.runId, 'workflow-current')
  assert.equal(pendingDagOwnerSessionId(lifecycle), 'session-current')
  assert.equal(
    restoredWorkflow?.summary.clarification?.taskPlan?.scopeTasks?.[0]?.id,
    'current-task'
  )
})

test('Backend restart 将 disk active 显式解析为 interrupted 且不展示生成中快照', () => {
  const lifecycle = {
    extensions: {
      planningRefresh: {
        schemaVersion: 'planning-refresh.v1',
        source: 'active_planning_run',
        status: 'planning_run_interrupted',
        planningRunId: 'planning-interrupted',
        workflowRunId: 'workflow-interrupted',
        threadId: 'thread-interrupted',
        dagGeneration: snapshot({ planningRunId: 'planning-interrupted' }),
        message: 'Backend 已重启或运行已中断；Candidate 不会自动续跑。'
      }
    }
  } as unknown as ApplicationLifecycle

  assert.equal(planningRefreshInterruption(lifecycle)?.planningRunId, 'planning-interrupted')
  assert.equal(latestDagGenerationSnapshot([], lifecycle), undefined)
})

test('ConfirmedPlan 会压制同 PlanningRun 的 stale historical snapshot', () => {
  const historical = snapshot({ planningRunId: 'planning-promoted', revision: 7 })
  const lifecycle = {
    extensions: {
      planningRefresh: {
        schemaVersion: 'planning-refresh.v1',
        source: 'confirmed_plan',
        status: 'confirmed',
        planningRunId: 'planning-promoted',
        message: '已恢复当前 ConfirmedPlan。'
      }
    }
  } as unknown as ApplicationLifecycle
  const messages = [
    {
      id: 1,
      role: 'assistant',
      content: '',
      createdAt: 1,
      processSteps: [
        {
          id: 'workflow:prepare_build_tasks',
          kind: 'workflow',
          status: 'running',
          title: '生成',
          detail: '',
          sequence: 1,
          dagGeneration: historical
        }
      ]
    }
  ] as AgentChatMessage[]

  assert.equal(latestDagGenerationSnapshot(messages, lifecycle), undefined)
  assert.equal(pendingDagConfirmationExecution(lifecycle), undefined)
})

test('工作区没有 Pending 时不恢复任何历史 DAG 阶段产物', () => {
  const historical = snapshot({ planningRunId: 'planning-history', revision: 4 })
  const lifecycle = {
    extensions: {
      planningRefresh: {
        schemaVersion: 'planning-refresh.v1',
        source: 'none',
        status: 'idle',
        message: '当前没有可恢复的 Planning 或 Confirmation 状态。'
      }
    }
  } as unknown as ApplicationLifecycle
  const messages = [
    {
      id: 1,
      role: 'assistant',
      content: '',
      createdAt: 1,
      processSteps: [
        {
          id: 'historical-planning-progress',
          kind: 'workflow',
          status: 'completed',
          title: '旧 DAG',
          detail: '',
          sequence: 1,
          dagGeneration: historical
        }
      ]
    }
  ] as AgentChatMessage[]

  assert.equal(latestDagGenerationSnapshot(messages, lifecycle), undefined)
  assert.equal(pendingDagOwnerSessionId(lifecycle), undefined)
})

test('Abandon 请求复用 Backend DraftIdentity 且不发送 Workflow cancel', () => {
  const workflow = {
    runId: 'workflow-current',
    threadId: 'thread-current',
    events: [],
    summary: {
      status: 'requires_user_input',
      clarification: {
        mode: 'build_task_plan_confirmation',
        draftIdentity: {
          planningRunId: 'planning-current',
          draftDigest: 'a'.repeat(64)
        }
      }
    }
  } as unknown as WorkflowRunPayload
  const identity = currentDagConfirmationDraftIdentity(workflow)
  const forwarded = buildWorkflowForwardedProps({
    editorMode: 'frontend',
    sessionId: 'session-current',
    planControlAction: 'abandon',
    planControlRunId: workflow.runId,
    planningRunId: identity?.planningRunId,
    draftDigest: identity?.draftDigest
  })

  assert.deepEqual(identity, {
    planningRunId: 'planning-current',
    draftDigest: 'a'.repeat(64)
  })
  assert.equal(forwarded.planControlAction, 'abandon')
  assert.equal(forwarded.sessionId, 'session-current')
  assert.equal(forwarded.planControlRunId, 'workflow-current')
  assert.equal(forwarded.planningRunId, 'planning-current')
  assert.equal(forwarded.draftDigest, 'a'.repeat(64))
  assert.equal('cancelRunId' in forwarded, false)
})

test('Confirm 动作绑定 Backend DraftIdentity 供 Graph 精确确认', () => {
  const workflow = {
    runId: 'workflow-confirm',
    threadId: 'thread-confirm',
    events: [],
    summary: {
      status: 'requires_user_input',
      clarification: {
        mode: 'build_task_plan_confirmation',
        draftIdentity: {
          planningRunId: 'planning-confirm',
          draftDigest: 'd'.repeat(64)
        }
      }
    }
  } as unknown as WorkflowRunPayload
  const bound = bindDagConfirmationDraftIdentity(workflow, {
    mode: 'build_task_plan_confirmation',
    action: 'confirm'
  })

  assert.deepEqual(bound, {
    mode: 'build_task_plan_confirmation',
    action: 'confirm',
    planningRunId: 'planning-confirm',
    draftDigest: 'd'.repeat(64)
  })
})

test('Regenerate 动作绑定旧 Pending 身份并通过 Graph 协议提交', () => {
  const workflow = {
    runId: 'workflow-regenerate',
    threadId: 'thread-regenerate',
    events: [],
    summary: {
      status: 'requires_user_input',
      clarification: {
        mode: 'build_task_plan_confirmation',
        draftIdentity: {
          planningRunId: 'planning-old',
          draftDigest: 'e'.repeat(64)
        }
      }
    }
  } as unknown as WorkflowRunPayload

  assert.deepEqual(
    bindDagConfirmationDraftIdentity(workflow, {
      mode: 'build_task_plan_confirmation',
      action: 'regenerate'
    }),
    {
      mode: 'build_task_plan_confirmation',
      action: 'regenerate',
      planningRunId: 'planning-old',
      draftDigest: 'e'.repeat(64)
    }
  )
})

test('Confirm/Abandon/Regenerate 动作缺少服务端 DraftIdentity 时 fail closed', () => {
  const workflow = {
    runId: 'workflow-confirm',
    threadId: 'thread-confirm',
    events: [],
    summary: {
      status: 'requires_user_input',
      clarification: { mode: 'build_task_plan_confirmation' }
    }
  } as unknown as WorkflowRunPayload
  const actions = [
    { mode: 'build_task_plan_confirmation', action: 'confirm' },
    { mode: 'build_task_plan_confirmation', action: 'abandon' },
    { mode: 'build_task_plan_confirmation', action: 'regenerate' }
  ] as const

  for (const action of actions) {
    assert.equal(bindDagConfirmationDraftIdentity(workflow, action), undefined)
  }
})

test('DraftIdentity 不完整时 Planning result 动作同样 fail closed', () => {
  const workflow = {
    runId: 'workflow-incomplete',
    threadId: 'thread-incomplete',
    events: [],
    summary: {
      status: 'requires_user_input',
      clarification: {
        mode: 'build_task_plan_confirmation',
        draftIdentity: { planningRunId: 'planning-incomplete', draftDigest: 'not-a-digest' }
      }
    }
  } as unknown as WorkflowRunPayload
  const action = {
    mode: 'build_task_plan_confirmation',
    action: 'confirm'
  } as const

  assert.equal(currentDagConfirmationDraftIdentity(workflow), undefined)
  assert.equal(bindDagConfirmationDraftIdentity(workflow, action), undefined)
})

test('PendingPlanGuard 只由 pending_plan awaiting_confirmation projection 决定', () => {
  const lifecycleWithOnlyOldExecution = {
    activeExecutions: { 'workflow-old': pendingDagExecution() },
    extensions: {}
  } as unknown as ApplicationLifecycle
  assert.deepEqual(resolvePendingPlanGuard(lifecycleWithOnlyOldExecution), { locked: false })

  const pendingLifecycle = {
    activeExecutions: {},
    extensions: {
      planningRefresh: {
        schemaVersion: 'planning-refresh.v1',
        source: 'pending_plan',
        status: 'awaiting_confirmation',
        ownerSessionId: 'session-owner',
        planningRunId: 'planning-current',
        workflowRunId: 'workflow-current'
      }
    }
  } as unknown as ApplicationLifecycle
  assert.deepEqual(resolvePendingPlanGuard(pendingLifecycle), {
    locked: true,
    ownerSessionId: 'session-owner',
    planningRunId: 'planning-current',
    workflowRunId: 'workflow-current'
  })

  const nonPendingLifecycle = {
    activeExecutions: { 'workflow-old': pendingDagExecution() },
    extensions: {
      planningRefresh: {
        schemaVersion: 'planning-refresh.v1',
        source: 'pending_plan',
        status: 'idle',
        ownerSessionId: 'session-owner'
      }
    }
  } as unknown as ApplicationLifecycle
  assert.deepEqual(resolvePendingPlanGuard(nonPendingLifecycle), { locked: false })
})

test('PendingPlanGuard 缺少 owner 时保留 invalid locked projection 供上层记录但不推导归属', () => {
  const lifecycle = {
    extensions: {
      planningRefresh: {
        schemaVersion: 'planning-refresh.v1',
        source: 'pending_plan',
        status: 'awaiting_confirmation',
        planningRunId: 'planning-ownerless',
        workflowRunId: 'workflow-ownerless'
      }
    }
  } as unknown as ApplicationLifecycle

  assert.deepEqual(resolvePendingPlanGuard(lifecycle), {
    locked: true,
    ownerSessionId: undefined,
    planningRunId: 'planning-ownerless',
    workflowRunId: 'workflow-ownerless'
  })
})

test('Pending Ready 才提供 Abandon，GENERATING lifecycle 没有结果级控制入口', () => {
  const pendingLifecycle = {
    activeExecutions: {},
    extensions: {
      planningRefresh: {
        schemaVersion: 'planning-refresh.v1',
        source: 'pending_plan',
        status: 'awaiting_confirmation',
        planningRunId: 'planning-pending',
        workflowRunId: 'workflow-pending',
        ownerSessionId: 'session-pending',
        draftDigest: 'c'.repeat(64),
        confirmation: {
          mode: 'build_task_plan_confirmation',
          actionValues: ['confirm', 'abandon', 'regenerate'],
          draftIdentity: {
            ownerSessionId: 'session-pending',
            planningRunId: 'planning-pending',
            draftDigest: 'c'.repeat(64)
          },
          taskPlan: { confirmationStatus: 'pending', scopeTasks: [] }
        },
        message: '待确认。'
      }
    }
  } as unknown as ApplicationLifecycle
  const generatingLifecycle = {
    activeExecutions: {},
    extensions: {
      planningRefresh: {
        schemaVersion: 'planning-refresh.v1',
        source: 'active_planning_run',
        status: 'planning',
        planningRunId: 'planning-generating',
        dagGeneration: snapshot({ planningRunId: 'planning-generating' }),
        message: '生成中。'
      }
    }
  } as unknown as ApplicationLifecycle

  const pendingExecution = pendingDagConfirmationExecution(pendingLifecycle, 'thread-pending')
  const pendingWorkflow = pendingDagConfirmationWorkflow([], pendingExecution, pendingLifecycle)
  assert.deepEqual(pendingWorkflow?.summary.clarification?.actionValues, [
    'confirm',
    'abandon',
    'regenerate'
  ])
  assert.equal(pendingDagOwnerSessionId(pendingLifecycle), 'session-pending')
  assert.equal(pendingDagConfirmationExecution(generatingLifecycle), undefined)
})

test('PendingPlan recovery 缺少 threadId 仍按 owner、WorkflowRunId 和 DraftIdentity 激活确认卡', () => {
  const planningRefresh = {
    schemaVersion: 'planning-refresh.v1',
    source: 'pending_plan',
    status: 'awaiting_confirmation',
    planningRunId: 'planning-recovered',
    workflowRunId: 'workflow-recovered',
    ownerSessionId: 'session-owner',
    draftDigest: 'a'.repeat(64),
    buildExecutionScope: { type: 'page', targetId: 'orders' },
    confirmation: {
      mode: 'build_task_plan_confirmation',
      status: 'requires_user_input',
      draftIdentity: {
        ownerSessionId: 'session-owner',
        planningRunId: 'planning-recovered',
        draftDigest: 'a'.repeat(64)
      },
      taskPlan: { confirmationStatus: 'pending', scopeTasks: [] }
    },
    message: '已从 PendingPlan 恢复待确认任务规划。'
  }
  const lifecycle = {
    application: { id: 'app-recovered' },
    revision: 12,
    updatedAt: '2026-09-11T00:00:00Z',
    activeExecutions: {},
    extensions: { planningRefresh }
  } as unknown as ApplicationLifecycle

  // Backend 不返回 threadId；只使用 owner session 已有的真实 thread 恢复 renderer execution。
  assert.equal(pendingDagConfirmationExecution(lifecycle), undefined)
  const recoveredExecution = pendingDagConfirmationExecution(lifecycle, 'thread-owner')
  const recoveredWorkflow = pendingDagConfirmationWorkflow([], recoveredExecution, lifecycle)
  assert.equal(recoveredExecution?.threadId, 'thread-owner')
  assert.equal(recoveredWorkflow?.runId, 'workflow-recovered')
  assert.equal(workflowInteractionAvailability(recoveredWorkflow!, lifecycle), 'active')

  const clarification = recoveredWorkflow!.summary.clarification!
  const ownerMismatchWorkflow = {
    ...recoveredWorkflow,
    summary: {
      ...recoveredWorkflow!.summary,
      clarification: {
        ...clarification,
        draftIdentity: { ...clarification.draftIdentity, ownerSessionId: 'session-other' }
      }
    }
  } as unknown as WorkflowRunPayload
  assert.equal(workflowInteractionAvailability(ownerMismatchWorkflow, lifecycle), 'stale')

  const draftMismatchWorkflow = {
    ...recoveredWorkflow,
    summary: {
      ...recoveredWorkflow!.summary,
      clarification: {
        ...clarification,
        draftIdentity: { ...clarification.draftIdentity, draftDigest: 'b'.repeat(64) }
      }
    }
  } as unknown as WorkflowRunPayload
  assert.equal(workflowInteractionAvailability(draftMismatchWorkflow, lifecycle), 'stale')

  assert.equal(
    workflowInteractionAvailability(
      { ...recoveredWorkflow, runId: 'workflow-other' } as WorkflowRunPayload,
      lifecycle
    ),
    'stale'
  )

  const threadBoundLifecycle = {
    ...lifecycle,
    extensions: { planningRefresh: { ...planningRefresh, threadId: 'thread-other' } }
  } as unknown as ApplicationLifecycle
  assert.equal(workflowInteractionAvailability(recoveredWorkflow!, threadBoundLifecycle), 'stale')
})

test('旧 planning refresh 不能否决已经进入 awaiting_user 的 DAG execution', () => {
  const execution = pendingDagExecution()
  const lifecycle = {
    application: { id: 'app-dag-transition', name: 'App' },
    updatedAt: '2026-09-10T00:00:01Z',
    revision: 7,
    initialization: { stage: 'ready_for_workbench', status: 'completed' },
    activeExecutions: { [execution.runId]: execution },
    extensions: {
      planningRefresh: {
        schemaVersion: 'planning-refresh.v1',
        source: 'active_planning_run',
        status: 'planning',
        planningRunId: 'planning-dag-transition',
        workflowRunId: 'workflow-dag-transition',
        threadId: 'thread-dag-transition',
        message: '上一帧仍在生成。'
      }
    }
  } as unknown as ApplicationLifecycle

  assert.equal(pendingDagConfirmationExecution(lifecycle)?.runId, execution.runId)
})

test('实时 lifecycle 缺少 refresh 时按最新 revision 选择 DAG 确认，旧 Pending 不抢会话锁', () => {
  const current = pendingDagExecution()
  const stale = {
    ...current,
    runId: 'workflow-stale-pending',
    threadId: 'thread-stale-pending',
    updatedAt: '2026-09-09T23:59:59Z',
    pendingInteraction: {
      ...current.pendingInteraction!,
      id: 'interaction-stale-pending',
      basedOnRevision: 32,
      payload: {
        mode: 'build_task_plan_confirmation'
      }
    }
  }
  const lifecycle = {
    application: { id: 'app-dag-transition', name: 'App' },
    updatedAt: '2026-09-10T00:00:02Z',
    revision: 81,
    initialization: { stage: 'ready_for_workbench', status: 'completed' },
    activeExecutions: {
      [stale.runId]: stale,
      [current.runId]: {
        ...current,
        updatedAt: '2026-09-10T00:00:01Z',
        pendingInteraction: {
          ...current.pendingInteraction!,
          basedOnRevision: 81
        }
      }
    },
    extensions: {}
  } as unknown as ApplicationLifecycle

  assert.equal(pendingDagConfirmationExecution(lifecycle)?.runId, current.runId)
  assert.equal(pendingDagOwnerSessionId(lifecycle), 'session-dag-transition')
})

test('DAG 确认卡后出现新消息仍按 DraftIdentity 与 lifecycle 保持可提交', () => {
  const execution = pendingDagExecution()
  const lifecycle = {
    application: { id: 'app-dag-transition', name: 'App' },
    updatedAt: '2026-09-10T00:00:01Z',
    revision: 7,
    initialization: { stage: 'ready_for_workbench', status: 'completed' },
    activeExecutions: { [execution.runId]: execution },
    extensions: {
      planningRefresh: {
        schemaVersion: 'planning-refresh.v1',
        source: 'active_planning_run',
        status: 'planning',
        planningRunId: 'planning-dag-transition',
        workflowRunId: 'workflow-dag-transition',
        threadId: 'thread-dag-transition',
        message: '上一帧仍在生成。'
      }
    }
  } as unknown as ApplicationLifecycle
  const workflow = dagConfirmationWorkflow(lifecycle)

  // Workflow 与 application lifecycle 的 interaction id/revision 故意不同，业务身份仍一致。
  assert.equal(workflowInteractionAvailability(workflow, lifecycle), 'active')
  assert.equal(workflowMessageInteractionAvailability(workflow, lifecycle, true, false), 'active')
})

test('同 run/thread 的旧 DAG 卡不能越过工作区唯一 Pending 的 DraftIdentity', () => {
  const execution = pendingDagExecution()
  const regeneratedDigest = '9'.repeat(64)
  const lifecycle = {
    application: { id: 'app-dag-transition', name: 'App' },
    updatedAt: '2026-09-10T00:00:02Z',
    revision: 8,
    initialization: { stage: 'ready_for_workbench', status: 'completed' },
    // 模拟同一 run/thread 的旧 execution 晚到；真正的 Pending 已由 refresh 给出新身份。
    activeExecutions: { [execution.runId]: execution },
    extensions: {
      planningRefresh: {
        schemaVersion: 'planning-refresh.v1',
        source: 'pending_plan',
        status: 'awaiting_confirmation',
        planningRunId: 'planning-regenerated',
        workflowRunId: execution.runId,
        ownerSessionId: 'session-dag-transition',
        draftDigest: regeneratedDigest,
        confirmation: {
          mode: 'build_task_plan_confirmation',
          draftIdentity: {
            ownerSessionId: 'session-dag-transition',
            planningRunId: 'planning-regenerated',
            draftDigest: regeneratedDigest
          },
          taskPlan: { confirmationStatus: 'pending', scopeTasks: [] }
        },
        message: '已恢复重新生成后的唯一 Pending。'
      }
    }
  } as unknown as ApplicationLifecycle
  const oldWorkflow = dagConfirmationWorkflow(lifecycle)
  const recoveredExecution = pendingDagConfirmationExecution(lifecycle)

  assert.equal(
    recoveredExecution?.pendingInteraction?.payload?.draftIdentity?.planningRunId,
    'planning-regenerated'
  )
  assert.equal(workflowInteractionAvailability(oldWorkflow, lifecycle), 'stale')
})

test('planningRefresh 独立校准但拒绝与当前 Pending execution 冲突的旧生成帧', () => {
  const execution = pendingDagExecution()
  const current = {
    application: { id: 'app-dag-transition', name: 'App' },
    updatedAt: '2026-09-10T00:00:01Z',
    revision: 7,
    initialization: { stage: 'ready_for_workbench', status: 'completed' },
    activeExecutions: { [execution.runId]: execution },
    extensions: {}
  } as unknown as ApplicationLifecycle
  const stalePlanningRefresh = {
    ...current,
    extensions: {
      planningRefresh: {
        schemaVersion: 'planning-refresh.v1',
        source: 'active_planning_run',
        status: 'planning',
        planningRunId: 'planning-dag-transition',
        workflowRunId: 'workflow-dag-transition',
        threadId: 'thread-dag-transition',
        message: '旧生成帧。'
      }
    }
  } as unknown as ApplicationLifecycle
  const lowerRevisionPendingRefresh = {
    ...current,
    revision: 6,
    activeExecutions: {},
    extensions: {
      planningRefresh: {
        schemaVersion: 'planning-refresh.v1',
        source: 'pending_plan',
        status: 'awaiting_confirmation',
        planningRunId: 'planning-dag-transition',
        workflowRunId: 'workflow-dag-transition',
        draftDigest: DAG_DRAFT_DIGEST,
        message: '磁盘 Pending 校准。'
      }
    }
  } as unknown as ApplicationLifecycle
  const lowerRevisionWrongPendingRefresh = {
    ...lowerRevisionPendingRefresh,
    extensions: {
      planningRefresh: {
        ...lowerRevisionPendingRefresh.extensions.planningRefresh,
        planningRunId: 'planning-stale',
        draftDigest: '8'.repeat(64)
      }
    }
  } as unknown as ApplicationLifecycle
  const lowerRevisionMissingIdentityRefresh = {
    ...lowerRevisionPendingRefresh,
    extensions: {
      planningRefresh: {
        ...lowerRevisionPendingRefresh.extensions.planningRefresh,
        draftDigest: undefined
      }
    }
  } as unknown as ApplicationLifecycle

  const withoutConflictingRefresh = latestApplicationLifecycle(current, stalePlanningRefresh)
  assert.equal(withoutConflictingRefresh.extensions.planningRefresh, undefined)
  assert.equal(withoutConflictingRefresh.activeExecutions[execution.runId], execution)

  const withIndependentRefresh = latestApplicationLifecycle(current, lowerRevisionPendingRefresh)
  assert.equal(withIndependentRefresh.revision, 7)
  assert.equal(withIndependentRefresh.activeExecutions[execution.runId], execution)
  assert.equal(withIndependentRefresh.extensions.planningRefresh?.status, 'awaiting_confirmation')

  const withoutWrongRefresh = latestApplicationLifecycle(current, lowerRevisionWrongPendingRefresh)
  assert.equal(withoutWrongRefresh.extensions.planningRefresh, undefined)

  const withoutMissingIdentityRefresh = latestApplicationLifecycle(
    current,
    lowerRevisionMissingIdentityRefresh
  )
  assert.equal(withoutMissingIdentityRefresh.extensions.planningRefresh, undefined)
})

/** 构造只携带 planningRefresh 投影的最小 lifecycle 帧。 */
function lifecycleWithPlanningRefresh(
  revision: number,
  planningRefresh?: Record<string, unknown>
): ApplicationLifecycle {
  return {
    application: { id: 'app-planning-refresh' },
    updatedAt: `2026-09-10T00:00:${String(revision).padStart(2, '0')}Z`,
    revision,
    initialization: { stage: 'ready_for_workbench', status: 'completed' },
    activeExecutions: {},
    extensions: planningRefresh ? { planningRefresh } : {}
  } as unknown as ApplicationLifecycle
}

/** 构造 generation-complete 或普通 confirmation 的最小 Workflow 快照。 */
function workflowWithClarificationMode(mode: string, suffix: string): WorkflowRunPayload {
  return {
    runId: `workflow-${suffix}`,
    threadId: `thread-${suffix}`,
    events: [],
    summary: {
      status: 'requires_user_input',
      clarification: { mode }
    }
  } as unknown as WorkflowRunPayload
}

test('首次 DAG generation-complete 触发 lifecycle refresh 并取得 pending projection', async () => {
  const finalWorkflow = workflowWithClarificationMode(
    'build_task_plan_confirmation',
    'generation-a'
  )
  const pendingLifecycle = lifecycleWithPlanningRefresh(21, {
    schemaVersion: 'planning-refresh.v1',
    source: 'pending_plan',
    status: 'awaiting_confirmation',
    planningRunId: 'planning-a',
    workflowRunId: finalWorkflow.runId,
    ownerSessionId: 'session-a',
    message: '已读取当前 PendingPlan。'
  })
  let refreshCount = 0
  let refreshedLifecycle: ApplicationLifecycle | undefined
  const refreshed = await maybeRefreshPendingPlanLifecycleAfterGeneration(
    finalWorkflow,
    { stopped: false },
    async () => {
      refreshCount += 1
      refreshedLifecycle = pendingLifecycle
    }
  )

  assert.equal(refreshed, true)
  assert.equal(refreshCount, 1)
  assert.equal(refreshedLifecycle?.extensions.planningRefresh?.source, 'pending_plan')
  assert.equal(refreshedLifecycle?.extensions.planningRefresh?.status, 'awaiting_confirmation')
})

test('Abandon 后再次手动生成仍刷新，Confirm/Abandon/Regenerate 不重复走 generation refresh', async () => {
  const refreshes: string[] = []
  // 用两次独立的普通 generation 模拟 Abandon 后再次手动生成。
  const generate = (suffix: string): Promise<boolean> =>
    maybeRefreshPendingPlanLifecycleAfterGeneration(
      workflowWithClarificationMode('build_task_plan_confirmation', suffix),
      { stopped: false },
      async () => {
        refreshes.push(suffix)
      }
    )

  assert.equal(await generate('generation-a'), true)
  assert.equal(
    await maybeRefreshPendingPlanLifecycleAfterGeneration(
      workflowWithClarificationMode('build_task_plan_confirmation', 'abandon-a'),
      { stopped: false, planControlAction: 'abandon' },
      async () => {
        refreshes.push('abandon-a')
      }
    ),
    false
  )
  assert.equal(await generate('generation-b'), true)

  for (const action of ['confirm', 'regenerate'] as const) {
    assert.equal(
      await maybeRefreshPendingPlanLifecycleAfterGeneration(
        workflowWithClarificationMode('build_task_plan_confirmation', action),
        {
          stopped: false,
          clarificationAnswers: {
            build_task_plan_confirmation: {
              mode: 'build_task_plan_confirmation',
              action
            }
          }
        },
        async () => {
          refreshes.push(action)
        }
      ),
      false
    )
  }

  assert.deepEqual(refreshes, ['generation-a', 'generation-b'])
})

test('普通非 DAG confirmation 不触发 generation-complete lifecycle refresh', async () => {
  let refreshCount = 0
  const refreshed = await maybeRefreshPendingPlanLifecycleAfterGeneration(
    workflowWithClarificationMode('test_phase_confirmation', 'ordinary-confirmation'),
    { stopped: false },
    async () => {
      refreshCount += 1
    }
  )

  assert.equal(refreshed, false)
  assert.equal(refreshCount, 0)
})

test('higher revision lifecycle 缺少 planningRefresh 时保留 none/idle projection', () => {
  const current = lifecycleWithPlanningRefresh(7, {
    schemaVersion: 'planning-refresh.v1',
    source: 'none',
    status: 'idle',
    message: '当前没有 PendingPlan。'
  })
  const incoming = lifecycleWithPlanningRefresh(8)

  const merged = latestApplicationLifecycle(current, incoming)

  assert.equal(merged.revision, 8)
  assert.equal(merged.extensions.planningRefresh?.source, 'none')
  assert.equal(merged.extensions.planningRefresh?.status, 'idle')
})

test('higher revision lifecycle 缺少 planningRefresh 时保留 pending projection', () => {
  const current = lifecycleWithPlanningRefresh(7, {
    schemaVersion: 'planning-refresh.v1',
    source: 'pending_plan',
    status: 'awaiting_confirmation',
    planningRunId: 'planning-current',
    workflowRunId: 'workflow-current',
    ownerSessionId: 'session-current',
    buildExecutionScope: { type: 'application', targetId: 'application' },
    message: '已恢复 PendingPlan。'
  })
  const incoming = lifecycleWithPlanningRefresh(8)

  const merged = latestApplicationLifecycle(current, incoming)

  assert.equal(merged.revision, 8)
  assert.equal(merged.extensions.planningRefresh?.source, 'pending_plan')
  assert.equal(merged.extensions.planningRefresh?.status, 'awaiting_confirmation')
  assert.equal(merged.extensions.planningRefresh?.ownerSessionId, 'session-current')
})

test('higher revision lifecycle 缺少 planningRefresh 时清理已被 Pending execution 越过的 active projection', () => {
  const execution = pendingDagExecution()
  const current = {
    ...lifecycleWithPlanningRefresh(7, {
      schemaVersion: 'planning-refresh.v1',
      source: 'active_planning_run',
      status: 'planning',
      planningRunId: 'planning-dag-transition',
      workflowRunId: execution.runId,
      threadId: execution.threadId,
      draftDigest: DAG_DRAFT_DIGEST,
      message: '旧生成投影。'
    }),
    activeExecutions: { [execution.runId]: execution }
  } as unknown as ApplicationLifecycle
  const incoming = lifecycleWithPlanningRefresh(8)

  const merged = latestApplicationLifecycle(current, incoming)

  assert.equal(merged.revision, 8)
  assert.equal(merged.extensions.planningRefresh, undefined)
})

test('higher revision lifecycle 明确返回 none/idle 时替换 pending projection', () => {
  const current = lifecycleWithPlanningRefresh(7, {
    schemaVersion: 'planning-refresh.v1',
    source: 'pending_plan',
    status: 'awaiting_confirmation',
    planningRunId: 'planning-current',
    workflowRunId: 'workflow-current',
    ownerSessionId: 'session-current',
    message: '已恢复 PendingPlan。'
  })
  const incoming = lifecycleWithPlanningRefresh(8, {
    schemaVersion: 'planning-refresh.v1',
    source: 'none',
    status: 'idle',
    message: '当前没有 PendingPlan。'
  })

  const merged = latestApplicationLifecycle(current, incoming)

  assert.equal(merged.extensions.planningRefresh?.source, 'none')
  assert.equal(merged.extensions.planningRefresh?.status, 'idle')
})

test('higher revision lifecycle 明确返回 pending_plan 时替换 none/idle projection', () => {
  const current = lifecycleWithPlanningRefresh(7, {
    schemaVersion: 'planning-refresh.v1',
    source: 'none',
    status: 'idle',
    message: '当前没有 PendingPlan。'
  })
  const incoming = lifecycleWithPlanningRefresh(8, {
    schemaVersion: 'planning-refresh.v1',
    source: 'pending_plan',
    status: 'awaiting_confirmation',
    planningRunId: 'planning-new',
    workflowRunId: 'workflow-new',
    ownerSessionId: 'session-new',
    message: '已恢复 PendingPlan。'
  })

  const merged = latestApplicationLifecycle(current, incoming)

  assert.equal(merged.extensions.planningRefresh?.source, 'pending_plan')
  assert.equal(merged.extensions.planningRefresh?.status, 'awaiting_confirmation')
  assert.equal(merged.extensions.planningRefresh?.ownerSessionId, 'session-new')
})

test('authoritative Abandon 在 reload、stale snapshot 与 late progress 后都不复活 Pending', () => {
  const abandonedLifecycle = {
    revision: 11,
    extensions: {
      planningRefresh: {
        schemaVersion: 'planning-refresh.v1',
        source: 'abandoned',
        status: 'abandoned',
        planningRunId: 'planning-abandoned',
        workflowRunId: 'workflow-abandoned',
        draftDigest: 'b'.repeat(64),
        buildExecutionScope: { type: 'page', targetId: 'orders' },
        message: '当前 Planning result 已放弃。'
      }
    }
  } as unknown as ApplicationLifecycle
  const lateSnapshot = snapshot({
    planningRunId: 'planning-abandoned',
    revision: 999,
    status: 'active',
    phase: 'generating_units'
  })
  const staleWorkflow = {
    runId: 'workflow-abandoned',
    threadId: 'thread-abandoned',
    events: [],
    summary: {
      status: 'requires_user_input',
      clarification: {
        mode: 'build_task_plan_confirmation',
        taskPlan: { confirmationStatus: 'pending', scopeTasks: [] }
      }
    }
  } as unknown as WorkflowRunPayload
  const messages = [
    {
      id: 1,
      role: 'assistant',
      content: '',
      createdAt: 1,
      workflow: staleWorkflow,
      processSteps: [
        {
          id: 'late-planning-progress',
          kind: 'workflow',
          status: 'running',
          title: '旧进度',
          detail: '',
          sequence: 99,
          dagGeneration: lateSnapshot
        }
      ]
    }
  ] as AgentChatMessage[]

  assert.equal(pendingDagConfirmationExecution(abandonedLifecycle), undefined)
  assert.equal(latestDagGenerationSnapshot(messages, abandonedLifecycle), undefined)
  assert.equal(latestDagGenerationSnapshot([...messages], abandonedLifecycle), undefined)
})

test('authoritative 终态会否决晚到的旧 DAG awaiting execution 与确认卡', () => {
  const execution = pendingDagExecution()
  const lifecycle = {
    application: { id: 'app-dag-transition', name: 'App' },
    updatedAt: '2026-09-10T00:00:02Z',
    revision: 8,
    initialization: { stage: 'ready_for_workbench', status: 'completed' },
    activeExecutions: { [execution.runId]: execution },
    extensions: {
      planningRefresh: {
        schemaVersion: 'planning-refresh.v1',
        source: 'abandoned',
        status: 'abandoned',
        planningRunId: 'planning-dag-transition',
        workflowRunId: 'workflow-dag-transition',
        draftDigest: DAG_DRAFT_DIGEST,
        message: '已放弃。'
      }
    }
  } as unknown as ApplicationLifecycle
  const workflow = dagConfirmationWorkflow(lifecycle)

  assert.equal(pendingDagConfirmationExecution(lifecycle), undefined)
  assert.equal(workflowInteractionAvailability(workflow, lifecycle), 'stale')
  assert.equal(workflowMessageInteractionAvailability(workflow, lifecycle, false, false), 'stale')
})

test('Abandon lifecycle revision 拒绝更晚到达的旧 Pending lifecycle event', () => {
  const abandoned = {
    application: { id: 'app-1' },
    revision: 12,
    extensions: {
      planningRefresh: {
        schemaVersion: 'planning-refresh.v1',
        source: 'abandoned',
        status: 'abandoned',
        planningRunId: 'planning-abandoned',
        message: '已放弃。'
      }
    }
  } as unknown as ApplicationLifecycle
  const stalePending = {
    application: { id: 'app-1' },
    revision: 11,
    extensions: {
      planningRefresh: {
        schemaVersion: 'planning-refresh.v1',
        source: 'pending_plan',
        status: 'awaiting_confirmation',
        planningRunId: 'planning-abandoned',
        message: '旧 Pending。'
      }
    }
  } as unknown as ApplicationLifecycle

  assert.equal(latestApplicationLifecycle(abandoned, stalePending), abandoned)
})

test('same revision 重连校准可刷新 planningRefresh，但不能覆盖持久化 lifecycle 字段', () => {
  const current = {
    application: { id: 'app-1', name: 'App' },
    updatedAt: '2026-09-08T00:00:00Z',
    revision: 12,
    initialization: { stage: 'ready_for_workbench', status: 'completed' },
    activeExecutions: {},
    extensions: {
      sentinel: 'persisted',
      planningRefresh: {
        schemaVersion: 'planning-refresh.v1',
        source: 'active_planning_run',
        status: 'planning',
        planningRunId: 'planning-current',
        workflowRunId: 'workflow-current',
        message: '运行中。'
      }
    }
  } as unknown as ApplicationLifecycle
  const recalibrated = {
    ...current,
    extensions: {
      sentinel: 'persisted',
      planningRefresh: {
        schemaVersion: 'planning-refresh.v1',
        source: 'active_planning_run',
        status: 'planning_run_interrupted',
        planningRunId: 'planning-current',
        workflowRunId: 'workflow-current',
        message: '运行已中断。'
      }
    }
  } as unknown as ApplicationLifecycle

  const merged = latestApplicationLifecycle(current, recalibrated)
  assert.equal(merged.revision, 12)
  assert.equal(merged.extensions.sentinel, 'persisted')
  assert.equal(merged.extensions.planningRefresh?.status, 'planning_run_interrupted')

  const sameRevisionWithoutRefresh = {
    ...recalibrated,
    extensions: { sentinel: 'persisted' }
  } as unknown as ApplicationLifecycle
  assert.equal(
    latestApplicationLifecycle(merged, sameRevisionWithoutRefresh).extensions.planningRefresh
      ?.status,
    'planning_run_interrupted'
  )
})
