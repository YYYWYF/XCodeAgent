import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
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
  latestDagGenerationSnapshot,
  pendingDagConfirmationExecution,
  pendingDagConfirmationWorkflow
} from '../src/renderer/src/components/AiChatPanel/stageOutputState'
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
