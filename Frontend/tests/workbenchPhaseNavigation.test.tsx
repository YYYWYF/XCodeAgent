import assert from 'node:assert/strict'
import { test } from 'node:test'
import { renderToStaticMarkup } from 'react-dom/server'
import WorkbenchTopBar from '../src/renderer/src/components/WorkbenchTopBar'
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
import type { ApplicationLifecycle, TestEntryGate } from '../src/renderer/src/typings'

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

test('顶部开发进度与测试门禁都按完整产物目录判断', () =>
  withStorage(() => {
    const lifecycle: ApplicationLifecycle = {
      application: { id: 'three-artifacts', name: 'three-artifacts' },
      updatedAt: '',
      revision: 1,
      initialization: { stage: 'ready_for_workbench', status: 'completed' },
      activeExecutions: {},
      testEntryGate: { ...allowed, total: 3, completed: 3 },
      developmentArtifacts: {
        pages: { home: { initialDevelopmentStatus: 'completed' } },
        endpoints: { age: { save: { initialDevelopmentStatus: 'completed' } } },
        entities: { AgeRecord: { initialDevelopmentStatus: 'completed' } }
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
    assert.match(html, /开发阶段<span>3\/3<\/span>/)
    assert.equal(lifecycle.testEntryGate?.total, 3)

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

test('历史版本隐藏顶部右侧的 Agent 身份、跟随开关与预览开关', () =>
  withStorage(() => {
    // 当前版本：三样都在，行为不变。
    const current = renderNavigation('one', 'acceptance_review')
    assert.ok(current.html.includes('workbench-topbar-agent'), '当前版本应展示 Agent 身份')
    assert.ok(current.html.includes('workbench-topbar-follow'), '当前版本应展示跟随开关')
    assert.ok(current.html.includes('workbench-topbar-preview-toggle'), '当前版本应展示预览开关')

    // 历史版本：这三样都指向"当前迭代的推进"，回看时无意义，整组隐藏。
    const historical = renderNavigation('one', 'acceptance_review', allowed, true, true)
    assert.ok(!historical.html.includes('workbench-topbar-agent'), '历史版本不应展示 Agent 身份')
    assert.ok(!historical.html.includes('workbench-topbar-follow'), '历史版本不应展示跟随开关')
    assert.ok(
      !historical.html.includes('workbench-topbar-preview-toggle'),
      '历史版本不应展示预览开关'
    )
  }))
