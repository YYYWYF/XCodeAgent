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
  planningRefreshInterruption
} from '../src/renderer/src/components/AiChatPanel/stageOutputState'
import { latestApplicationLifecycle } from '../src/renderer/src/hooks/useApplicationLifecycleStore'
import type { AgentChatMessage } from '../src/renderer/src/components/AiChatPanel/types'
import type { ApplicationLifecycle, WorkflowRunPayload } from '../src/renderer/src/typings'

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
        source: 'pending',
        status: 'awaiting_confirmation',
        planningRunId: 'planning-current',
        workflowRunId: 'workflow-current',
        threadId: 'thread-current',
        draftDigest: 'd'.repeat(64),
        buildExecutionScope: { type: 'page', targetId: 'orders' },
        confirmation: {
          mode: 'build_task_plan_confirmation',
          status: 'requires_user_input',
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

test('Confirm 动作缺少服务端 DraftIdentity 时保持原样', () => {
  const workflow = {
    runId: 'workflow-confirm',
    threadId: 'thread-confirm',
    events: [],
    summary: {
      status: 'requires_user_input',
      clarification: { mode: 'build_task_plan_confirmation' }
    }
  } as unknown as WorkflowRunPayload
  const action = {
    mode: 'build_task_plan_confirmation',
    action: 'confirm'
  } as const

  assert.deepEqual(bindDagConfirmationDraftIdentity(workflow, action), action)
})

test('Pending Ready 才提供 Abandon，GENERATING lifecycle 没有结果级控制入口', () => {
  const pendingLifecycle = {
    activeExecutions: {},
    extensions: {
      planningRefresh: {
        schemaVersion: 'planning-refresh.v1',
        source: 'pending',
        status: 'awaiting_confirmation',
        planningRunId: 'planning-pending',
        workflowRunId: 'workflow-pending',
        threadId: 'thread-pending',
        draftDigest: 'c'.repeat(64),
        confirmation: {
          mode: 'build_task_plan_confirmation',
          actionValues: ['confirm', 'abandon'],
          draftIdentity: {
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

  const pendingExecution = pendingDagConfirmationExecution(pendingLifecycle)
  const pendingWorkflow = pendingDagConfirmationWorkflow([], pendingExecution, pendingLifecycle)
  assert.deepEqual(pendingWorkflow?.summary.clarification?.actionValues, ['confirm', 'abandon'])
  assert.equal(pendingDagConfirmationExecution(generatingLifecycle), undefined)
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
        source: 'pending',
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
    latestApplicationLifecycle(merged, sameRevisionWithoutRefresh).extensions.planningRefresh?.status,
    'planning_run_interrupted'
  )
})
