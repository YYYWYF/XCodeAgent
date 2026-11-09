import assert from 'node:assert/strict'
import './workbenchEntry.test'
import './workbenchPlanningCatalog.test'
import './stoppedPlanningAvailability.test'
import './stoppedPlanningNotice.test'
import { test } from 'node:test'
import { renderToStaticMarkup } from 'react-dom/server'
import WorkbenchTopBar from '../src/renderer/src/components/WorkbenchTopBar'
import { planningStageAccess, planningStageEntryTransferred, stoppedPlanningForSession } from '../src/renderer/src/components/AiChatPanel/planningStageAccess'
import { planningMessageAgentPhase } from '../src/renderer/src/components/AiChatPanel/components/MessageList/planningMessageWorkflow'
import EndedTaskNotice from '../src/renderer/src/components/AiChatPanel/components/NextDevelopmentGuide/EndedTaskNotice'
import { latestApplicationLifecycle } from '../src/renderer/src/service/activeApplicationPlanning'
import PlanningHistoryNotice from '../src/renderer/src/components/AiChatPanel/components/PlanningHistoryNotice'
import { WorkbenchPhaseProvider } from '../src/renderer/src/context/WorkbenchPhaseContext'
import {
  useWorkbenchPhase,
  type WorkbenchPhaseContextValue
} from '../src/renderer/src/context/workbenchPhaseState'
import { clearApplicationWorkbenchState } from '../src/renderer/src/workbenchPhase'
import {
  getReachedWorkbenchPhase,
  reachedPhaseFromSessions
} from '../src/renderer/src/workbenchPhaseNavigation'
import type { ApplicationLifecycle, TestEntryGate, WorkflowRunPayload } from '../src/renderer/src/typings'

test('首次创建停止且文件变化时留在原阶段重新生成，正式修订仍返回开发', () => {
  for (const initial of [true, false]) {
    const html = renderToStaticMarkup(<EndedTaskNotice mode="stopped_generation" unchanged={false}
      restartInitialPlanning={initial} continuing={false} disabled={false}
      onContinue={async () => {}} onRestart={() => {}} />)
    assert.equal(html.includes('返回开发阶段'), !initial)
    assert.equal(html.includes('按当前文件重新生成'), initial)
    assert.equal(html.includes('完成后仍需审阅确认'), initial)
  }
})

test('入口交接后断网及冷启动仍在当前计划线程开放原门，来源会话保持只读', () => {
  const lifecycle: ApplicationLifecycle = {
    application: { id: 'app', name: 'app' }, revision: 6, updatedAt: '',
    initialization: { stage: 'awaiting_planning_stage_entry', status: 'awaiting_user', threadId: 'plan' },
    activeExecutions: {}, extensions: {}
  }
  const workflow = { threadId: 'plan', summary: { phase: 'planning_stage_entry' },
    state: { planning_stage_handoff: { targetPhase: 'planning' } } } as WorkflowRunPayload
  const transferred = planningStageEntryTransferred(workflow, lifecycle)
  assert.equal(transferred, true)
  const access = planningStageAccess({ phase: 'planning', lifecycle, enteredDevelopment: false,
    sessionThreadId: 'plan', pendingStageEntryTransferred: transferred })
  assert.equal(access.stageActive, true)
  assert.equal(access.historyOnly, false)
  assert.equal(access.transitioning, undefined)
  assert.equal(planningMessageAgentPhase(workflow, 'planning'), 'planning')
  assert.equal(planningMessageAgentPhase({ ...workflow, state: {} }, 'planning'), 'product')
  const history = planningStageAccess({ phase: 'product', lifecycle, enteredDevelopment: false,
    sessionThreadId: 'design', pendingStageEntryTransferred: transferred })
  assert.equal(history.historyOnly, true)
  assert.equal(history.destination, 'planning')
  assert.equal(planningStageEntryTransferred({ ...workflow, threadId: 'old' }, lifecycle), false)
  assert.equal(planningStageEntryTransferred({ ...workflow, state: {} }, lifecycle), false)
  assert.equal(planningStageEntryTransferred(workflow, { ...lifecycle,
    initialization: { ...lifecycle.initialization, stage: 'generating_requirement_document' } }), false)
})

const allowed: TestEntryGate = {
  allowed: true,
  total: 2,
  completed: 2,
  pending: 0,
  inProgress: 0,
  blockers: []
}

/** 阶段覆盖与浏览进度都按「应用 + 版本」作用域，测试统一用这个版本。 */
const VERSION = 'v1'

test('正式修订停止只在原需求会话显示继续卡，历史确认仍只读', () => {
  const lifecycle: ApplicationLifecycle = {
    application: { id: 'app', name: 'app' }, revision: 3, updatedAt: '',
    initialization: { stage: 'ready_for_workbench', status: 'completed', threadId: 'design' },
    activeExecutions: {}, extensions: {}, endedPlanningExecution: {
      runId: 'stopped', threadId: 'design', stage: 'generating_requirement_document',
      workspaceUnchanged: true, canRestart: false
    }
  }
  const session = { threadId: 'design' }
  assert.ok(stoppedPlanningForSession({ phase: 'product', lifecycle, session }))
  assert.equal(stoppedPlanningForSession({ phase: 'planning', lifecycle, session }), undefined)
  assert.equal(stoppedPlanningForSession({ phase: 'product', lifecycle, session: { threadId: 'old' } }), undefined)
  assert.equal(planningStageAccess({ phase: 'product', lifecycle, enteredDevelopment: true,
    sessionThreadId: 'design' }).historyOnly, true)
  const html = renderToStaticMarkup(<EndedTaskNotice mode="stopped_generation" unchanged
    continuing={false} disabled={false} onContinue={async () => undefined} />)
  assert.ok(html.includes('继续生成'))
  const changed = renderToStaticMarkup(<EndedTaskNotice mode="stopped_generation" unchanged={false}
    continuing={false} disabled={false} onContinue={async () => undefined} />)
  assert.ok(changed.includes('工作区已变化'))
  assert.ok(changed.includes('无法继续原任务'))
  assert.ok(!changed.match(/<button\b[^>]*>[\s\S]*?<\/button>/g)?.some((button) => button.includes('继续生成')))
  const changedWithReturn = renderToStaticMarkup(<EndedTaskNotice mode="stopped_generation" unchanged={false}
    continuing={false} disabled={false} onContinue={async () => assert.fail('失效规划不能继续')}
    onRestart={() => undefined} />)
  assert.ok(changedWithReturn.includes('返回开发阶段'))
  assert.ok(!changedWithReturn.includes('重新发起任务'))
  const checking = renderToStaticMarkup(<EndedTaskNotice mode="stopped_generation" checkingWorkspace
    continuing={false} disabled={false} onContinue={async () => undefined} />)
  assert.ok(checking.includes('正在检查工作区状态'))
  assert.ok(!checking.includes('继续生成'))
  const refreshed = latestApplicationLifecycle(lifecycle, {
    ...lifecycle, endedExecutionsCheckedAt: '2026-11-04T00:00:01Z',
    endedPlanningExecution: { ...lifecycle.endedPlanningExecution!, workspaceUnchanged: false }
  })
  assert.equal(refreshed.endedPlanningExecution?.workspaceUnchanged, false)
})

/** 隔离本地存储，覆盖多个应用与组件重新挂载时的真实持久化行为。 */
function withStorage(run: () => void): void {
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'window')
  const values = new Map<string, string>()
  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: {
      localStorage: {
        getItem: (key: string) => values.get(key) ?? null,
        setItem: (key: string, value: string) => values.set(key, value),
        removeItem: (key: string) => values.delete(key),
        // 按前缀批量清理需要完整的 Storage 语义（length + key(index)）。
        get length(): number {
          return values.size
        },
        key: (index: number) => Array.from(values.keys())[index] ?? null
      }
    }
  })
  try {
    run()
  } finally {
    if (previous) Object.defineProperty(globalThis, 'window', previous)
    else Reflect.deleteProperty(globalThis, 'window')
  }
}

/** 渲染真实顶部按钮并取得 Provider 操作，模拟切阶段后的重新打开。 */
function renderNavigation(
  applicationId = 'one',
  executionPhase?: string,
  gate = allowed,
  locked = false,
  versionReadOnly = false
): { html: string; context: WorkbenchPhaseContextValue } {
  const lifecycle: ApplicationLifecycle = {
    application: { id: applicationId, name: applicationId },
    updatedAt: '',
    revision: 1,
    initialization: { stage: 'ready_for_workbench', status: 'completed' },
    testEntryGate: gate,
    activeExecutions: executionPhase
      ? {
          run: {
            scope: 'application',
            targetId: applicationId,
            threadId: 'thread',
            runId: 'run',
            phase: executionPhase,
            status: 'awaiting_user',
            startedAt: '',
            updatedAt: ''
          }
        }
      : {}
  }
  let context: WorkbenchPhaseContextValue | undefined
  /** 捕获真实 Context，避免用手工构造的入口权限绕过被测逻辑。 */
  function Navigation(): JSX.Element {
    context = useWorkbenchPhase()
    return (
      <WorkbenchTopBar
        application={lifecycle.application}
        workspaceRoot="/workspace"
        onReturnWelcome={() => assert.fail('不应切换应用')}
        rightPanelOpen
        onToggleRightPanel={() => assert.fail('不应切换预览')}
        versionReadOnly={versionReadOnly}
      />
    )
  }
  const html = renderToStaticMarkup(
    <WorkbenchPhaseProvider
      applicationId={applicationId}
      versionId={VERSION}
      lifecycle={lifecycle}
      locked={locked}
    >
      <Navigation />
    </WorkbenchPhaseProvider>
  )
  assert.ok(context)
  return { html, context }
}

/** 检查实际步骤条按钮可点击性，不依赖展示颜色或内部阶段排序实现。 */
function assertPhaseEnabled(html: string, label: string, enabled: boolean): void {
  const button = html
    .match(/<button\b[^>]*role="tab"[^>]*>[\s\S]*?<\/button>/g)
    ?.find((entry) => entry.includes(`${label}阶段`))
  assert.ok(button, `缺少${label}按钮`)
  assert.equal(!button.includes('disabled=""'), enabled, `${label}按钮状态`)
}

/** 检查步骤条高亮位置；已生成版本也要指明它停在哪个阶段。 */
function assertPhaseActive(html: string, label: string): void {
  const buttons = html.match(/<button\b[^>]*role="tab"[^>]*>[\s\S]*?<\/button>/g) ?? []
  const active = buttons.filter((entry) => entry.includes('aria-selected="true"'))
  assert.equal(active.length, 1, `应恰好高亮一个阶段，实际 ${active.length} 个`)
  assert.ok(active[0].includes(`${label}阶段`), `高亮阶段应为${label}`)
}

test('顶部开发进度与测试门禁只统计页面和 Endpoint，规划实体不参与', () =>
  withStorage(() => {
    const lifecycle: ApplicationLifecycle = {
      application: { id: 'three-artifacts', name: 'three-artifacts' },
      updatedAt: '',
      revision: 1,
      initialization: { stage: 'ready_for_workbench', status: 'completed' },
      activeExecutions: {},
      testEntryGate: { ...allowed, total: 2, completed: 2 },
      developmentArtifacts: {
        pages: { home: { initialDevelopmentStatus: 'completed' } },
        endpoints: { age: { save: { initialDevelopmentStatus: 'completed' } } },
        entities: { AgeRecord: { initialDevelopmentStatus: 'pending' } }
      }
    }
    const html = renderToStaticMarkup(
      <WorkbenchPhaseProvider
        applicationId="three-artifacts"
        versionId={VERSION}
        lifecycle={lifecycle}
      >
        <WorkbenchTopBar
          application={lifecycle.application}
          lifecycle={lifecycle}
          workspaceRoot="/workspace"
          onReturnWelcome={() => {}}
          rightPanelOpen={false}
          onToggleRightPanel={() => {}}
        />
      </WorkbenchPhaseProvider>
    )
    assert.match(html, /开发阶段<span>2\/2<\/span>/)
    assert.equal(lifecycle.testEntryGate?.total, 2)

    const newVersionHtml = renderToStaticMarkup(
      <WorkbenchPhaseProvider applicationId="new-version" versionId="v2" lifecycle={lifecycle}>
        <WorkbenchTopBar
          application={{ ...lifecycle.application, id: 'new-version' }}
          lifecycle={lifecycle}
          developmentTotals={{ completed: 0, total: 0 }}
          workspaceRoot="/workspace"
          onReturnWelcome={() => {}}
          rightPanelOpen={false}
          onToggleRightPanel={() => {}}
        />
      </WorkbenchPhaseProvider>
    )
    assert.match(newVersionHtml, /开发阶段<span>0\/0<\/span>/)
  }))

test('已生成版本（locked）高亮冻结阶段且全部阶段不可点', () =>
  withStorage(() => {
    // 历史版本冻结在验收：其 lifecycle 的 execution 停在 acceptance。
    const released = renderNavigation('one', 'acceptance_review', allowed, true)

    assert.equal(released.context.locked, true)
    assert.equal(released.context.phase, 'acceptance')
    // 高亮必须落在验收：否则阶段条上没有任何"这个版本停在哪"的提示。
    assertPhaseActive(released.html, '验收')
    // 只读定位不等于可点：六个阶段全部禁用。
    for (const label of ['设计', '计划', '开发', '测试', '审查', '验收']) {
      assertPhaseEnabled(released.html, label, false)
    }
  }))

test('当前迭代（未锁定）仍按生命周期高亮且可点', () =>
  withStorage(() => {
    const iterating = renderNavigation('one', 'acceptance_review')

    assert.equal(iterating.context.locked, false)
    assertPhaseActive(iterating.html, '验收')
    assertPhaseEnabled(iterating.html, '验收', true)
  }))

test('验收回到开发后，运行收口和重新打开都保留审查、验收入口', () =>
  withStorage(() => {
    renderNavigation('one', 'acceptance_review').context.switchPhase('development')
    const development = renderNavigation()
    assert.equal(development.context.phase, 'development')
    assert.equal(development.context.derivedPhase, 'development')
    assertPhaseEnabled(development.html, '审查', true)
    assertPhaseEnabled(development.html, '验收', true)
    development.context.switchPhase('review')
    const review = renderNavigation()
    assert.equal(review.context.phase, 'review')
    assertPhaseEnabled(review.html, '验收', true)
    review.context.switchPhase('acceptance')
    assert.equal(renderNavigation().context.phase, 'acceptance')
  }))

test('当前会话目录恢复已到达阶段，空验收会话不会误解锁', () =>
  withStorage(() => {
    const { context } = renderNavigation()
    context.recordReachedPhase(
      reachedPhaseFromSessions([
        { workbenchPhase: 'review', messageCount: 3 },
        { workbenchPhase: 'acceptance', messageCount: 0 }
      ])
    )
    assertPhaseEnabled(renderNavigation().html, '审查', true)
    assertPhaseEnabled(renderNavigation().html, '验收', false)
    context.recordReachedPhase(
      reachedPhaseFromSessions([{ workbenchPhase: 'acceptance', messageCount: 2 }])
    )
    const restored = renderNavigation()
    assert.equal(restored.context.phase, 'development')
    assertPhaseEnabled(restored.html, '验收', true)
  }))

test('应用进度互相隔离，未到达阶段仍禁用，删除应用清理浏览进度', () =>
  withStorage(() => {
    renderNavigation('one', 'acceptance_review').context.switchPhase('development')
    const other = renderNavigation('two')
    assertPhaseEnabled(other.html, '审查', false)
    assertPhaseEnabled(other.html, '验收', false)
    clearApplicationWorkbenchState('one')
    assert.equal(getReachedWorkbenchPhase('one', VERSION), 'product')
    assertPhaseEnabled(renderNavigation().html, '验收', false)
  }))

test('保留验收回访权限不能绕过当前测试门禁', () =>
  withStorage(() => {
    renderNavigation('one', 'acceptance_review').context.switchPhase('development')
    const closed = renderNavigation('one', undefined, { ...allowed, allowed: false })
    assertPhaseEnabled(closed.html, '测试', false)
    assertPhaseEnabled(closed.html, '审查', false)
    assertPhaseEnabled(closed.html, '验收', false)
    closed.context.switchPhase('test')
    closed.context.switchPhase('review')
    closed.context.switchPhase('acceptance')
    assert.equal(renderNavigation().context.phase, 'development')
    const staleReview = renderNavigation('one', 'code_review', { ...allowed, allowed: false })
    assertPhaseActive(staleReview.html, '开发')
  }))

test('顶部快捷区只保留主题切换，当前版本与只读版本都不重复提供面板控制', () =>
  withStorage(() => {
    // 当前顶栏已把面板布局操作交给右侧分隔条，只保留主题快捷入口。
    for (const view of [renderNavigation('one', 'acceptance_review'),
      renderNavigation('one', 'acceptance_review', allowed, true, true)]) {
      assert.ok(view.html.includes('workbench-topbar-theme-toggle'))
      assert.ok(!view.html.includes('workbench-topbar-agent'))
      assert.ok(!view.html.includes('workbench-topbar-follow'))
      assert.ok(!view.html.includes('workbench-topbar-preview-toggle'))
    }
  }))

/** 构造真实生命周期形状，验证可操作阶段不由手动浏览位置决定。 */
function planningLifecycle(stage: ApplicationLifecycle['initialization']['stage']): ApplicationLifecycle {
  return { application: { id: 'history-test', name: '阶段只读验证' }, revision: 1, updatedAt: '',
    initialization: { stage, status: 'awaiting_user', threadId: 'current-stage', iterationId: 'same-iteration' },
    activeExecutions: {} }
}

test('进入计划后需求仅可回看，当前计划可对话，旧计划会话不能操作当前确认', () => {
  const lifecycle = planningLifecycle('awaiting_technical_plan_confirmation')
  const input = { lifecycle, enteredDevelopment: false, sessionThreadId: 'current-stage' }
  const product = planningStageAccess({ ...input, phase: 'product' })
  assert.equal(product.historyOnly, true)
  assert.equal(product.destination, 'planning')
  assert.equal(planningStageAccess({ ...input, phase: 'planning' }).historyOnly, false)
  assert.equal(planningStageAccess({ ...input, phase: 'planning', sessionThreadId: 'old-plan' }).historyOnly, true)
  const html = renderToStaticMarkup(<PlanningHistoryNotice access={product} onReturn={() => undefined} />)
  assert.ok(html.includes('历史会话只读'))
  assert.ok(html.includes('当前计划阶段提出'))
  assert.ok(!html.includes('textarea'))
})

test('真实阶段交接在途时展示同步，完成换绑后恢复当前确认且历史门禁不放开', () => {
  const input = { phase: 'planning' as const, enteredDevelopment: false, sessionThreadId: 'new-plan',
    lifecycle: planningLifecycle('awaiting_planning_stage_entry') }
  const syncing = planningStageAccess({ ...input, enteringStage: true })
  assert.equal(syncing.historyOnly, true)
  assert.equal(syncing.transitioning, true)
  assert.equal(syncing.destination, undefined)
  const html = renderToStaticMarkup(<PlanningHistoryNotice access={syncing} onReturn={() => undefined} />)
  assert.ok(html.includes('正在进入计划阶段'))
  assert.ok(!html.includes('历史会话只读'))
  assert.ok(!html.includes('已返回需求阶段'))
  assert.ok(!html.includes('<button'))
  assert.equal(planningStageAccess({ ...input, enteringStage: false }).destination, 'product')
  const lifecycle = planningLifecycle('awaiting_technical_plan_confirmation')
  lifecycle.initialization.threadId = 'new-plan'
  const ready = planningStageAccess({ ...input, lifecycle, enteringStage: true })
  assert.equal(ready.historyOnly, false)
  assert.equal(ready.transitioning, undefined)
  assert.equal(planningStageAccess({ ...input, phase: 'product', enteringStage: true,
    lifecycle }).historyOnly, true)
})

test('开发及后续阶段始终关闭需求和计划自由输入，开发入口不随最远阶段关闭', () => {
  for (const executionPhase of ['build', 'integration_test', 'code_review', 'acceptance_review']) {
    const lifecycle = planningLifecycle('ready_for_workbench')
    lifecycle.activeExecutions = { run: { runId: 'run', threadId: 'development-thread',
      scope: 'application', targetId: 'history-test', phase: executionPhase, status: 'awaiting_user', startedAt: '', updatedAt: '' } }
    for (const phase of ['product', 'planning'] as const) {
      const access = planningStageAccess({ phase, lifecycle, enteredDevelopment: true })
      assert.equal(access.historyOnly, true)
      assert.equal(access.destination, 'development')
    }
    assert.equal(planningStageAccess({ phase: 'development', lifecycle, enteredDevelopment: true }).historyOnly, false)
  }
})

test('正式修改返回需求或计划时只重新开放当前阶段及权威线程', () => {
  for (const [stage, phase] of [['awaiting_requirement_document_confirmation', 'product'],
    ['awaiting_technical_plan_confirmation', 'planning']] as const) {
    const lifecycle = planningLifecycle(stage)
    const access = planningStageAccess({ phase, lifecycle, enteredDevelopment: true, sessionThreadId: 'current-stage' })
    assert.equal(access.stageActive, true)
    assert.equal(access.historyOnly, false)
    assert.equal(planningStageAccess({ phase, lifecycle, enteredDevelopment: true, sessionThreadId: 'old-stage' }).historyOnly, true)
    assert.equal(planningStageAccess({ phase: phase === 'product' ? 'planning' : 'product', lifecycle, enteredDevelopment: true }).historyOnly, true)
  }
})

test('模板首次就绪保留进入开发卡，进入开发后该计划阶段变成只读', () => {
  const lifecycle = planningLifecycle('ready_for_workbench')
  assert.equal(planningStageAccess({ phase: 'planning', lifecycle, enteredDevelopment: false }).historyOnly, false)
  assert.equal(planningStageAccess({ phase: 'planning', lifecycle, enteredDevelopment: true }).historyOnly, true)
})

test('首次需求会话沿明确入口绑定当前 Graph，不把初始展示线程误判成历史', () => {
  const lifecycle = planningLifecycle('awaiting_requirement_document_confirmation')
  const input = { phase: 'product' as const, lifecycle, enteredDevelopment: false,
    sessionThreadId: 'initial-visible-session', sessionEntryKey: 'current-stage' }
  assert.equal(planningStageAccess(input).historyOnly, false)
  assert.equal(planningStageAccess({ ...input, sessionEntryKey: 'previous-stage' }).historyOnly, true)
})

test('后端已有开发完成事实时不因本地进入标记缺失而重新开放计划阶段', () => {
  const lifecycle = planningLifecycle('ready_for_workbench')
  lifecycle.developmentArtifacts = { pages: { notice: { initialDevelopmentStatus: 'completed' } },
    endpoints: {}, entities: {} }
  const access = planningStageAccess({ phase: 'planning', lifecycle, enteredDevelopment: false })
  assert.equal(access.historyOnly, true)
  assert.equal(access.destination, 'development')
})
