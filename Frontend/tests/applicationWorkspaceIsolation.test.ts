import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { applicationWorkspaceKey, sameApplicationWorkspace } from '../src/renderer/src/service/applicationWorkspace'
import { mergeRecoveredApplicationPlanning, reduceApplicationPlanningCurrentState, type ApplicationPlanningCurrentState } from '../src/renderer/src/service/activeApplicationPlanning'
import { initialConnectionState } from '../src/renderer/src/service/connectionState'
import { ApplicationPlanningRuntime } from '../src/renderer/src/service/applicationPlanningRuntime'
import { useApplicationPlanningRuntimes, planningRuntimeKey } from '../src/renderer/src/hooks/useApplicationPlanningRuntimes'
import { useApplicationLifecycleStore } from '../src/renderer/src/hooks/useApplicationLifecycleStore'
import type { ApplicationConfig, ApplicationLifecycle } from '../src/renderer/src/typings'

/** 克隆保留项目 ID 和线程，仅本机目录不同；生成等待态不会在测试中启动模型。 */
function planning(workspaceRoot: string, revision = 1): ApplicationPlanningCurrentState {
  return { application: { id: 'same-project', appName: '克隆项目', workspaceRoot } as ApplicationConfig,
    threadId: 'same-thread', transportState: 'idle', connection: initialConnectionState(true),
    lifecycle: { application: { id: 'same-project', name: '克隆项目' }, revision, updatedAt: '2026-11-09T00:00:00Z',
      initialization: { threadId: 'same-thread', stage: 'collecting_requirement', status: 'awaiting_user' },
      activeExecutions: {}, extensions: {} } }
}

test('同 ID、同线程的克隆目录不能复用旧规划；同目录元数据更新仍保留实时状态', () => {
  const old = planning('/workspace/original', 99)
  old.error = '原目录的错误'
  const clone = planning('/workspace/clone', 2)
  assert.equal(sameApplicationWorkspace(old.application, clone.application), false)
  assert.equal(mergeRecoveredApplicationPlanning(old, clone), old)
  assert.notEqual(planningRuntimeKey(old.application.id, old.threadId, old.application.workspaceRoot),
    planningRuntimeKey(clone.application.id, clone.threadId, clone.application.workspaceRoot))
  assert.equal(applicationWorkspaceKey({ id: 'same-project', workspaceRoot: 'C:\\Work\\App\\' }),
    applicationWorkspaceKey({ id: 'same-project', workspaceRoot: 'c:/work/app' }))
  const same = mergeRecoveredApplicationPlanning(old, planning('/workspace/original/', 100))
  assert.equal(same.error, old.error)
  assert.equal(same.lifecycle.revision, 100)
  const late = reduceApplicationPlanningCurrentState(clone, { type: 'run_failed', applicationId: old.application.id,
    threadId: old.threadId, workspaceRoot: old.application.workspaceRoot, error: '原目录迟到错误' })
  assert.equal(late, clone)
})

test('生产 Runtime 在工作区切换后丢弃旧目录的在途响应，并拒绝再向旧目录发送保存', async () => {
  let current = planning('/workspace/old')
  let release!: () => void
  let saves = 0
  const waiting = new Promise<undefined>((resolve) => { release = () => resolve(undefined) })
  const runtime = new ApplicationPlanningRuntime({ applicationId: current.application.id, threadId: current.threadId,
    getCurrentState: () => current,
    dispatchCurrentEvent: (event) => { current = reduceApplicationPlanningCurrentState(current, event) },
    publishContent: () => {}, publishReply: () => {}, publishWorkflow: () => {},
    onTechnicalPlanConfirmed: async () => false, onRevisionContinuation: async () => {},
    session: { sendMessage: async () => { throw new Error('不应启动生成') }, stop: async () => {}, hasActiveRun: () => false },
    saveRequirementSpecDraft: async () => { saves++; throw new Error('不应保存旧目录') },
    readAuthoritativeSnapshot: () => waiting })
  try {
    const pending = runtime.reconcileCurrentState()
    const clone = planning('/workspace/new', 1)
    current = clone
    release()
    await pending
    assert.equal(current, clone)
    await assert.rejects(runtime.saveRequirementSpec({}), /Runtime 已失效/)
    assert.equal(saves, 0)
  } finally { runtime.dispose() }
})

test('生产 Runtime 管理 Hook 为同 ID 克隆创建新实例，并收口旧目录的请求', async () => {
  const previousWindow = globalThis.window
  Object.defineProperty(globalThis, 'window', { configurable: true, writable: true, value: {
    setInterval: globalThis.setInterval, clearInterval: globalThis.clearInterval,
    addEventListener: () => {}, removeEventListener: () => {} } })
  let current = planning('/workspace/first')
  let manager!: ReturnType<typeof useApplicationPlanningRuntimes>
  let renderer: ReactTestRenderer | undefined
  let detached = 0, stopped = 0
  /** 挂载实际管理 Hook，只把 AG-UI 会话替换成可计数的隔离端口。 */
  function Probe() {
    manager = useApplicationPlanningRuntimes({ activePlannings: [current], getPlanningState: () => current,
      dispatchPlanningEvent: (event) => { current = reduceApplicationPlanningCurrentState(current, event) },
      publishContent: () => {}, publishReply: () => {}, publishWorkflow: () => {},
      onTechnicalPlanConfirmed: async () => false, onRevisionContinuation: async () => {},
      createSession: () => ({ sendMessage: async () => { throw new Error('不应生成') },
        stop: async () => { stopped++ }, hasActiveRun: () => false, detach: () => { detached++ } }) })
    return null
  }
  try {
    await act(async () => { renderer = create(createElement(Probe)) })
    const old = manager.getRuntime(current.application.id)!
    current = planning('/workspace/second')
    await act(async () => { renderer!.update(createElement(Probe)) })
    const next = manager.getRuntime(current.application.id)!
    assert.notEqual(next, old)
    assert.equal(next.workspaceRoot, '/workspace/second')
    assert.equal(detached, 0)
    assert.equal(stopped, 1)
  } finally {
    await act(async () => renderer?.unmount())
    Object.defineProperty(globalThis, 'window', { configurable: true, writable: true, value: previousWindow })
  }
})

test('生产 lifecycle Hook 不把原目录的高 revision 或迟到回调合并到克隆目录', async () => {
  let root = '/workspace/original'
  let state!: ReturnType<typeof useApplicationLifecycleStore>
  let renderer: ReactTestRenderer | undefined
  /** 挂载实际 store 并通过 props 模拟同项目的目录切换。 */
  function Probe() { state = useApplicationLifecycleStore('same-project', root); return null }
  try {
    await act(async () => { renderer = create(createElement(Probe)) })
    const oldMerge = state.mergeLifecycle
    await act(async () => oldMerge(planning(root, 99).lifecycle))
    root = '/workspace/clone'
    await act(async () => renderer!.update(createElement(Probe)))
    assert.equal(state.lifecycle, undefined)
    await act(async () => { oldMerge(planning('/workspace/original', 1000).lifecycle) })
    assert.equal(state.lifecycle, undefined)
    const clonedLifecycle: ApplicationLifecycle = planning(root, 2).lifecycle
    await act(async () => state.mergeLifecycle(clonedLifecycle))
    assert.equal(state.lifecycle?.revision, 2)
  } finally { await act(async () => renderer?.unmount()) }
})
