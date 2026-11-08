import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { UiReadVisibility } from '../src/renderer/src/hooks/uiReadVisibility'
import { useStoppedPlanningAvailability } from '../src/renderer/src/components/AiChatPanel/hooks/useStoppedPlanningAvailability'
import type { ApplicationLifecycle } from '../src/renderer/src/typings'

/** 构造同 revision 的实时资格，验证源码变化无需写入生命周期新版本。 */
function lifecycle(unchanged: boolean): ApplicationLifecycle {
  return {
    application: { id: 'app', name: 'test' }, revision: 1, updatedAt: '',
    initialization: { stage: 'ready_for_workbench', status: 'completed', threadId: 'planning' },
    activeExecutions: {}, endedPlanningExecution: {
      runId: 'stopped', threadId: 'planning', stage: 'generating_technical_plan',
      workspaceUnchanged: unchanged, canRestart: false
    }
  }
}

test('停止卡串行校准变更、点击前复验、失败关闭，隐藏和跨工作区迟到响应不回写', async () => {
  const previousWindow = Object.getOwnPropertyDescriptor(globalThis, 'window')
  const previousDocument = Object.getOwnPropertyDescriptor(globalThis, 'document')
  const windowEvents = new EventTarget()
  const documentEvents = new EventTarget()
  const timers = new Map<number, () => void>()
  let timerId = 0
  const documentMock = {
    hidden: false,
    addEventListener: documentEvents.addEventListener.bind(documentEvents),
    removeEventListener: documentEvents.removeEventListener.bind(documentEvents)
  }
  Object.defineProperty(globalThis, 'window', { configurable: true, value: {
    setTimeout: (callback: () => void) => { timers.set(++timerId, callback); return timerId },
    clearTimeout: (id?: number) => { if (id !== undefined) timers.delete(id) },
    addEventListener: windowEvents.addEventListener.bind(windowEvents),
    removeEventListener: windowEvents.removeEventListener.bind(windowEvents)
  } })
  Object.defineProperty(globalThis, 'document', { configurable: true, value: documentMock })
  const reads: Array<{ resolve: (value: ApplicationLifecycle) => void; reject: (error: Error) => void }> = []
  const published: ApplicationLifecycle[] = []
  let state!: ReturnType<typeof useStoppedPlanningAvailability>
  let root: ReactTestRenderer | undefined
  /** 使用真实 Hook，手动控制生命周期回执与时钟以复现外部源码变更。 */
  function Probe({ scope }: { scope: string }): JSX.Element {
    state = useStoppedPlanningAvailability({
      scopeKey: scope, runId: 'stopped', threadId: 'planning',
      read: () => new Promise((resolve, reject) => reads.push({ resolve, reject })),
      onLifecycle: (value) => { published.push(value) }
    })
    return <output />
  }
  /** 通过现有工作台可见性开关挂载，隐藏后不能继续后台扫描。 */
  const view = (scope: string, visible = true): JSX.Element =>
    createElement(UiReadVisibility.Provider, { value: visible }, createElement(Probe, { scope }))
  try {
    await act(async () => { root = create(view('one')) })
    assert.equal(reads.length, 1)
    assert.equal(state.unchanged, undefined)
    assert.equal(state.checking, true)
    await act(async () => reads[0].resolve(lifecycle(true)))
    assert.equal(state.unchanged, true)
    assert.equal(state.checking, false)
    assert.equal(timers.size, 0, '空闲期间不得安排定时检查')

    await act(async () => {
      windowEvents.dispatchEvent(new Event('focus'))
      windowEvents.dispatchEvent(new Event('focus'))
    })
    assert.equal(reads.length, 2, '慢读取期间不得重叠扫描')
    let check!: Promise<boolean>
    await act(async () => { check = state.check() })
    assert.equal(reads.length, 2, '点击必须等待旧扫描退出')
    await act(async () => reads[1].resolve(lifecycle(false)))
    assert.equal(reads.length, 3, '点击不能复用点击前的在途回执')
    await act(async () => { reads[2].resolve(lifecycle(false)); assert.equal(await check, false) })
    assert.equal(state.unchanged, false)
    assert.equal(published.at(-1)?.endedPlanningExecution?.workspaceUnchanged, false)
    assert.equal(timers.size, 0, '焦点与点击检查完成后也不安排定时扫描')

    await act(async () => { check = state.check() })
    assert.equal(reads.length, 4, '点击继续必须读取本次资格')
    await act(async () => { reads[3].resolve(lifecycle(false)); assert.equal(await check, false) })
    await act(async () => { check = state.check() })
    await act(async () => { reads[4].reject(new Error('offline')); assert.equal(await check, false) })
    assert.equal(state.unchanged, undefined)
    assert.equal(state.checking, false, '失败不能继续伪装成检查中')
    assert.ok(state.error?.includes('无法检查工作区'))

    await act(async () => root!.update(view('one', false)))
    assert.equal(timers.size, 0)
    await act(async () => windowEvents.dispatchEvent(new Event('focus')))
    assert.equal(reads.length, 5)
    assert.equal(await state.check(), false)

    await act(async () => root!.update(view('one')))
    assert.equal(reads.length, 6)
    const previousPublished = published.length
    await act(async () => root!.update(view('two')))
    assert.equal(reads.length, 7)
    await act(async () => reads[5].resolve(lifecycle(true)))
    assert.equal(published.length, previousPublished, '旧工作区响应不能回写')
    assert.equal(state.unchanged, undefined)
    await act(async () => reads[6].resolve(lifecycle(false)))
    assert.equal(state.unchanged, false)

    await act(async () => {
      documentMock.hidden = true
      documentEvents.dispatchEvent(new Event('visibilitychange'))
    })
    assert.equal(await state.check(), false)
    assert.equal(reads.length, 7)
  } finally {
    await act(async () => root?.unmount())
    if (previousWindow) Object.defineProperty(globalThis, 'window', previousWindow)
    else Reflect.deleteProperty(globalThis, 'window')
    if (previousDocument) Object.defineProperty(globalThis, 'document', previousDocument)
    else Reflect.deleteProperty(globalThis, 'document')
  }
})
