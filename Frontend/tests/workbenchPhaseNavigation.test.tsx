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
        removeItem: (key: string) => values.delete(key)
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
  gate = allowed
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
      />
    )
  }
  const html = renderToStaticMarkup(
    <WorkbenchPhaseProvider applicationId={applicationId} lifecycle={lifecycle}>
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
    assert.equal(getReachedWorkbenchPhase('one'), 'product')
    assertPhaseEnabled(renderNavigation().html, '验收', false)
  }))

test('保留验收回访权限不能绕过当前测试门禁', () =>
  withStorage(() => {
    renderNavigation('one', 'acceptance_review').context.switchPhase('development')
    const closed = renderNavigation('one', undefined, { ...allowed, allowed: false })
    assertPhaseEnabled(closed.html, '测试', false)
    assertPhaseEnabled(closed.html, '验收', true)
    closed.context.switchPhase('test')
    assert.equal(renderNavigation().context.phase, 'development')
  }))
