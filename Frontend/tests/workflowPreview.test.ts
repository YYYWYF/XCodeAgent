import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  requiresInitialDetailDesignSelection,
  workflowFinalResultPresentation,
  workflowPreviewTarget
} from '../src/renderer/src/components/AiChatPanel/utils'
import {
  deriveDisplayedPlanExecutionMode,
  derivePlanExecutionMode,
  planExecutionContextForPage,
  planExecutionForPage,
  planExecutionShowsDebugResume,
  withWorkflowExecutionStatus,
  workflowResumeNode
} from '../src/renderer/src/components/AiChatPanel/planExecutionMode'
import {
  APPLICATIONS_CHANGED_EVENT,
  canOpenApplicationWorkbench,
  isApplicationCreationComplete,
  subscribeApplicationsChanged
} from '../src/renderer/src/service/applicationStorage'
import { readApplicationLifecycle } from '../src/renderer/src/service/agUiAgent'
import {
  hasNonTerminalApplicationExecution,
  latestApplicationLifecycle
} from '../src/renderer/src/hooks/useApplicationLifecycleStore'
import { navigatePreviewHistory } from '../src/renderer/src/utils/previewUrl'
import type {
  ApplicationLifecycle,
  WorkbenchExecution,
  WorkflowRunPayload
} from '../src/renderer/src/typings'

/** 构造指定运行状态的最小 Workflow 预览测试数据。 */
function previewWorkflow(
  overrides: Partial<WorkflowRunPayload['summary']> = {},
  runId = 'run-1'
): WorkflowRunPayload {
  return {
    runId,
    threadId: 'thread-1',
    events: [],
    summary: {
      phase: 'launch_project',
      status: 'requires_user_input',
      previewUrl: 'http://127.0.0.1:3000',
      ...overrides
    }
  }
}

test('实时成功 launch 会生成可去重的预览目标', () => {
  const target = workflowPreviewTarget(previewWorkflow(), true)

  assert.equal(target?.url, 'http://127.0.0.1:3000')
  assert.equal(target?.key, 'thread-1:run-1:http://127.0.0.1:3000')
})

test('历史、失败、非启动阶段和缺少地址的 Workflow 不触发预览', () => {
  assert.equal(workflowPreviewTarget(previewWorkflow(), false), undefined)
  assert.equal(workflowPreviewTarget(previewWorkflow({ status: 'failed' }), true), undefined)
  assert.equal(
    workflowPreviewTarget(previewWorkflow({ phase: 'integration_test' }), true),
    undefined
  )
  assert.equal(workflowPreviewTarget(previewWorkflow({ previewUrl: '' }), true), undefined)
})

test('不同运行返回相同 URL 时仍生成不同的一次性目标', () => {
  const first = workflowPreviewTarget(previewWorkflow({}, 'run-1'), true)
  const second = workflowPreviewTarget(previewWorkflow({}, 'run-2'), true)

  assert.notEqual(first?.key, second?.key)
  assert.equal(first?.url, second?.url)
})

test('已有任一页面设计的工作区重新进入时不再显示首次设计挡板', () => {
  assert.equal(requiresInitialDetailDesignSelection(true), false)
  assert.equal(requiresInitialDetailDesignSelection(false), true)
})

test('最终结果标题区分成功和失败 Workflow', () => {
  assert.deepEqual(workflowFinalResultPresentation(previewWorkflow()), {
    failed: false,
    title: '任务已完成'
  })
  assert.deepEqual(workflowFinalResultPresentation(previewWorkflow({ status: 'failed' })), {
    failed: true,
    title: '任务执行失败'
  })
})

test('只有初始化完成阶段允许 lifecycle 直接放行工作台', () => {
  const lifecycle = planLifecycle(pageExecution())

  assert.equal(isApplicationCreationComplete(lifecycle), true)
  lifecycle.initialization.stage = 'awaiting_project_plan_confirmation'
  assert.equal(isApplicationCreationComplete(lifecycle), false)
})

test('应用计划确认标记永久放行工作台且不依赖后续 lifecycle', () => {
  const application = {
    id: 'app-1',
    source: 'new',
    planningConfirmedAt: 1
  } as Parameters<typeof canOpenApplicationWorkbench>[0]
  const unrelatedPagePlanningLifecycle = planLifecycle(pageExecution())
  unrelatedPagePlanningLifecycle.initialization.stage = 'awaiting_project_plan_confirmation'

  assert.equal(canOpenApplicationWorkbench(application), true)
  assert.equal(canOpenApplicationWorkbench(application, unrelatedPagePlanningLifecycle), true)
})

test('未写入永久确认标记的新应用仍可由当前初始化完成状态放行', () => {
  const application = {
    id: 'app-1',
    source: 'new'
  } as Parameters<typeof canOpenApplicationWorkbench>[0]

  assert.equal(canOpenApplicationWorkbench(application), false)
  assert.equal(canOpenApplicationWorkbench(application, planLifecycle(pageExecution())), true)
})

test('最近项目订阅会响应应用索引变化并在清理后停止响应', () => {
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, 'window')
  const eventTarget = new EventTarget()
  let changeCount = 0

  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: eventTarget
  })
  try {
    const unsubscribe = subscribeApplicationsChanged(() => {
      changeCount += 1
    })

    eventTarget.dispatchEvent(new Event(APPLICATIONS_CHANGED_EVENT))
    assert.equal(changeCount, 1)

    unsubscribe()
    eventTarget.dispatchEvent(new Event(APPLICATIONS_CHANGED_EVENT))
    assert.equal(changeCount, 1)
  } finally {
    if (originalWindow) Object.defineProperty(globalThis, 'window', originalWindow)
    else Reflect.deleteProperty(globalThis, 'window')
  }
})

test('实时 lifecycle revision 不会被较旧的文件校准结果覆盖', () => {
  const execution = pageExecution()
  const realtime = planLifecycle(execution)
  realtime.revision = 8
  const staleFileSnapshot = planLifecycle(execution)
  staleFileSnapshot.revision = 7

  assert.equal(latestApplicationLifecycle(realtime, staleFileSnapshot), realtime)
})

test('只有非终态 execution 会阻止工作台直接返回首页', () => {
  const runningLifecycle = planLifecycle(pageExecution({ status: 'running' }))
  const awaitingLifecycle = planLifecycle(pageExecution({ status: 'awaiting_user' }))
  const completedLifecycle = planLifecycle(pageExecution({ status: 'completed' }))

  assert.equal(hasNonTerminalApplicationExecution(runningLifecycle), true)
  assert.equal(hasNonTerminalApplicationExecution(awaitingLifecycle), true)
  assert.equal(hasNonTerminalApplicationExecution(completedLifecycle), false)
})

test('独立 AG-UI lifecycle 事件只接收完整的版本化投影', () => {
  const lifecycle = planLifecycle(pageExecution())

  assert.equal(readApplicationLifecycle(lifecycle), lifecycle)
  assert.equal(readApplicationLifecycle({ revision: 3 }), undefined)
})

test('重复地址不追加历史，新地址会截断旧前进记录', () => {
  const initial = {
    history: ['https://first.example', 'https://second.example'],
    index: 0
  }
  const duplicate = navigatePreviewHistory(initial, 'https://first.example')
  const next = navigatePreviewHistory(initial, '127.0.0.1:3000')

  assert.equal(duplicate, initial)
  assert.deepEqual(next, {
    history: ['https://first.example', 'http://127.0.0.1:3000'],
    index: 1
  })
})

/** 构造页面计划执行测试需要的最小生命周期。 */
function planLifecycle(execution: WorkbenchExecution): ApplicationLifecycle {
  return {
    schemaVersion: '1.2.0',
    application: { id: 'app-1', name: '测试应用' },
    updatedAt: '2026-07-23T00:00:00Z',
    revision: 2,
    initialization: {
      stage: 'ready_for_workbench',
      status: 'completed'
    },
    activeExecutions: { [execution.runId]: execution },
    extensions: {}
  }
}

/** 构造不同页面标识和 Workflow 身份下的执行快照。 */
function pageExecution(overrides: Partial<WorkbenchExecution> = {}): WorkbenchExecution {
  return {
    scope: 'page',
    targetId: 'page-orders',
    pageId: 'page-orders',
    threadId: 'thread-orders',
    runId: 'run-orders',
    phase: 'build',
    status: 'running',
    startedAt: '2026-07-23T00:00:00Z',
    updatedAt: '2026-07-23T00:00:00Z',
    ...overrides
  }
}

test('代码生成和集成测试阶段始终保持计划控制模式', () => {
  assert.equal(derivePlanExecutionMode(pageExecution({ phase: 'build' })), 'running')
  assert.equal(derivePlanExecutionMode(pageExecution({ phase: 'integration_test' })), 'running')
})

test('等待用户确认的 Workflow 即使暂缺 execution 快照也不会恢复输入框', () => {
  assert.equal(deriveDisplayedPlanExecutionMode(undefined, 'requires_user_input', false), 'running')
  assert.equal(deriveDisplayedPlanExecutionMode(undefined, 'completed', false), 'idle')
})

test('权威生命周期已移除 execution 时忽略历史取消快照并恢复输入框', () => {
  assert.equal(deriveDisplayedPlanExecutionMode(undefined, 'cancelled', false, true), 'idle')
  assert.equal(
    deriveDisplayedPlanExecutionMode(undefined, 'requires_user_input', false, true),
    'idle'
  )
})

test('当前请求仍在运行时即使生命周期暂为空也保持输入锁', () => {
  assert.equal(deriveDisplayedPlanExecutionMode(undefined, 'running', true, true), 'running')
})

test('本地停止请求在 Workflow 快照到达前立即进入停止 loading', () => {
  assert.equal(deriveDisplayedPlanExecutionMode(undefined, 'stopping', true, true), 'stopping')
})

test('本地停止状态优先于尚未刷新的运行中生命周期快照', () => {
  const execution = pageExecution()

  assert.equal(deriveDisplayedPlanExecutionMode(execution, 'stopping', true), 'stopping')
  assert.equal(deriveDisplayedPlanExecutionMode(execution, 'stopped', false), 'stopped')
})

test('暂停后的调试窗口默认选中最近完成的可恢复节点', () => {
  const workflow = previewWorkflow()
  workflow.events = [
    { type: 'workflow.node.completed', timestamp: '', nodeName: 'prepare_build_tasks' },
    { type: 'workflow.run.finished', timestamp: '', nodeName: 'handle_failure' }
  ]

  assert.equal(workflowResumeNode(workflow, 'build'), 'prepare_build_tasks')
  assert.equal(workflowResumeNode(undefined, 'integration_test'), 'integration_test')
})

test('节点调试恢复入口覆盖两种可恢复暂停态但不绕过结构化确认', () => {
  assert.equal(planExecutionShowsDebugResume('stopped'), true)
  assert.equal(planExecutionShowsDebugResume('awaiting_plan_adjustment'), true)
  assert.equal(planExecutionShowsDebugResume('awaiting_authorization'), false)
  assert.equal(planExecutionShowsDebugResume('awaiting_repair_confirmation'), false)
  assert.equal(planExecutionShowsDebugResume('awaiting_acceptance'), false)
})

test('乐观停止只更新目标 execution，不覆盖创建生命周期', () => {
  const execution = pageExecution()
  const lifecycle = planLifecycle(execution)
  const workflow = previewWorkflow({ lifecycle })
  const stopped = withWorkflowExecutionStatus(workflow, 'stopped', execution.runId)

  assert.equal(stopped?.summary.status, 'stopped')
  assert.equal(stopped?.summary.lifecycle?.activeExecutions[execution.runId].status, 'stopped')
  assert.equal(stopped?.summary.lifecycle?.initialization.stage, 'ready_for_workbench')
  assert.equal(stopped?.summary.lifecycle?.initialization.status, 'completed')
})

test('Workflow 异常终态进入失败控制模式而不是自由输入', () => {
  assert.equal(deriveDisplayedPlanExecutionMode(undefined, 'failed', false), 'failed')
})

test('页面 ID 历史前缀差异不会让未完成计划恢复自由输入', () => {
  const execution = pageExecution()
  const matched = planExecutionForPage(planLifecycle(execution), 'orders')

  assert.equal(matched?.runId, execution.runId)
  assert.equal(derivePlanExecutionMode(matched), 'running')
})

test('恢复运行按 Workflow 身份兜底且不会匹配另一个页面', () => {
  const execution = pageExecution({ pageId: 'legacy-orders', targetId: 'legacy-orders' })
  const lifecycle = planLifecycle(execution)

  assert.equal(planExecutionForPage(lifecycle, 'customers'), undefined)
  assert.equal(
    planExecutionForPage(lifecycle, 'orders', { threadId: 'thread-orders' })?.runId,
    'run-orders'
  )
})

test('生命周期资源登记不会让关联页面进入只读执行模式', () => {
  const execution = pageExecution()
  const lifecycle = planLifecycle(execution)
  lifecycle.resourceLocks = {
    pages: {
      'page-orders': {
        runId: execution.runId,
        ownerPageId: 'page-orders',
        mode: 'exclusive',
        role: 'primary',
        reason: 'primary_target',
        acquiredAt: execution.startedAt
      },
      'order-detail': {
        runId: execution.runId,
        ownerPageId: 'page-orders',
        mode: 'exclusive',
        role: 'dependency',
        reason: 'plan_dependency',
        acquiredAt: execution.startedAt
      }
    },
    apiContracts: {},
    dataSources: {}
  }

  const context = planExecutionContextForPage(lifecycle, 'page-order-detail')

  assert.equal(context.execution, undefined)
  assert.equal(context.dependencyLocked, false)
  assert.equal(derivePlanExecutionMode(context.execution), 'idle')
})

test('不在资源集合中的页面仍可自由输入', () => {
  const execution = pageExecution()
  const lifecycle = planLifecycle(execution)
  lifecycle.resourceLocks = {
    pages: {
      orders: {
        runId: execution.runId,
        ownerPageId: 'orders',
        mode: 'exclusive',
        role: 'primary',
        reason: 'primary_target',
        acquiredAt: execution.startedAt
      }
    },
    apiContracts: {
      'orders-api': {
        runId: execution.runId,
        ownerPageId: 'orders',
        mode: 'exclusive',
        role: 'dependency',
        reason: 'plan_dependency',
        acquiredAt: execution.startedAt
      }
    },
    dataSources: {}
  }

  const context = planExecutionContextForPage(lifecycle, 'help')

  assert.equal(context.execution, undefined)
  assert.equal(context.dependencyLocked, false)
})
