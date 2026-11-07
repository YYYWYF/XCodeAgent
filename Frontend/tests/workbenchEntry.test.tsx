import assert from 'node:assert/strict'
import { test } from 'node:test'
import { act, create } from 'react-test-renderer'
import { message } from 'antd'
import { useWorkbenchEntry } from '../src/renderer/src/pages/useWorkbenchEntry'
import {
  MISSING_RESTORE_SESSION_MESSAGE,
  workbenchEntryFailureMessage,
  type WorkbenchEntryFailure
} from '../src/renderer/src/pages/workbenchEntryMessages'

// 使用可控时钟渲染真实入口 Hook，验证超时和放行时序而无需实际等待一分钟。
function withEntry(run: (input: {
  current: () => ReturnType<typeof useWorkbenchEntry>
  advance: (ms: number) => void
  notices: string[]
  failures: () => number
}) => void): void {
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, 'window')
  const originalNow = Date.now
  const originalError = message.error
  const originalWarn = console.warn
  let now = 0
  let sequence = 0
  let failureCount = 0
  const timers = new Map<number, { at: number; callback: () => void }>()
  const notices: string[] = []
  let value!: ReturnType<typeof useWorkbenchEntry>
  // 记录导航回调次数，以确认多个错误或迟到回执不会重复处理。
  const onFailure = (): void => { failureCount += 1 }
  // 只挂载入口状态，不涉及真实项目文件或模型调用。
  function Harness(): null {
    value = useWorkbenchEntry(onFailure)
    return null
  }
  Object.defineProperty(globalThis, 'window', { configurable: true, value: {
    setTimeout: (callback: () => void, delay: number): number => {
      const id = ++sequence
      timers.set(id, { at: now + delay, callback })
      return id
    },
    clearTimeout: (id: number): void => { timers.delete(id) }
  } })
  Date.now = () => now
  message.error = ((content: string) => { notices.push(content) }) as typeof message.error
  console.warn = () => undefined
  let renderer: ReturnType<typeof create> | undefined
  try {
    act(() => { renderer = create(<Harness />) })
    run({
      current: () => value, notices, failures: () => failureCount,
      advance: (ms: number): void => {
        const target = now + ms
        while (true) {
          const next = [...timers].filter(([, timer]) => timer.at <= target)
            .sort((a, b) => a[1].at - b[1].at)[0]
          if (!next) break
          now = next[1].at
          timers.delete(next[0])
          act(() => next[1].callback())
        }
        now = target
      }
    })
  } finally {
    act(() => renderer?.unmount())
    Date.now = originalNow
    message.error = originalError
    console.warn = originalWarn
    if (originalWindow) Object.defineProperty(globalThis, 'window', originalWindow)
    else Reflect.deleteProperty(globalThis, 'window')
  }
}

test('入口等待满 60 秒才失败，超时准确列出尚未完成的资源', () => {
  withEntry(({ current, advance, notices, failures }) => {
    act(() => current().markConfigurationLoaded())
    advance(59_999)
    assert.equal(failures(), 0)
    advance(1)
    assert.equal(failures(), 1)
    assert.match(notices[0], /规划内容、历史对话仍未加载完成/)
    assert.ok(!notices[0].includes('应用配置'))
    assert.ok(!notices[0].includes('已返回首页'))
  })
})

test('已完成规划检查后仅历史对话未完成时，超时不误报规划内容', () => {
  withEntry(({ current, advance, notices }) => {
    act(() => { current().markConfigurationLoaded(); current().markPlanningArtifactsLoaded() })
    advance(60_000)
    assert.match(notices[0], /历史对话仍未加载完成/)
    assert.ok(!notices[0].includes('规划内容'))
  })
})

test('全部加载完成后保留淡出动画，后续错误与超时不再触发导航', () => {
  withEntry(({ current, advance, failures }) => {
    act(() => {
      current().markConfigurationLoaded()
      current().markPlanningArtifactsLoaded()
      current().handleSessionHistoryReadyChange(true)
    })
    advance(519)
    assert.equal(current().entryStage, 'loading')
    advance(1)
    assert.equal(current().entryStage, 'leaving')
    advance(280)
    assert.equal(current().entryStage, 'ready')
    act(() => current().failWorkbenchEntry('planning'))
    advance(60_000)
    assert.equal(failures(), 0)
  })
})

/** 配置和目录并行读取后，不能因目录先返回就提前结束整体入口门禁。 */
test('目录与历史已返回但配置未完成时继续等待配置', () => {
  withEntry(({ current, advance }) => {
    act(() => { current().markPlanningArtifactsLoaded(); current().handleSessionHistoryReadyChange(true) })
    advance(800)
    assert.equal(current().entryStage, 'loading')
    act(() => current().markConfigurationLoaded())
    advance(280)
    assert.equal(current().entryStage, 'ready')
  })
})

test('会话技术异常不泄露到提示，多个失败和迟到完成只处理一次', () => {
  withEntry(({ current, advance, notices, failures }) => {
    act(() => {
      current().handleSessionHistoryReadyChange(false, 'Error: IPC ENOENT /private/project/session.json')
      current().failWorkbenchEntry('planning')
      current().markConfigurationLoaded()
      current().markPlanningArtifactsLoaded()
      current().handleSessionHistoryReadyChange(true)
    })
    advance(60_000)
    assert.deepEqual(notices, ['无法恢复历史对话，请重新打开应用后重试。'])
    assert.equal(failures(), 1)
    assert.equal(current().entryStage, 'loading')
  })
})

test('目标会话不存在时使用对应业务提示', () => {
  withEntry(({ current, notices }) => {
    act(() => current().handleSessionHistoryReadyChange(false, MISSING_RESTORE_SESSION_MESSAGE))
    assert.deepEqual(notices, ['要恢复的对话已不存在，请重新打开应用并选择其他对话。'])
  })
})

test('入口全部失败类别都只显示业务原因和建议', () => {
  const reasons: WorkbenchEntryFailure[] = [
    'workspace_missing', 'configuration', 'planning', 'session_history', 'session_missing', 'unknown'
  ]
  for (const reason of reasons) {
    withEntry(({ current, notices, failures }) => {
      act(() => current().failWorkbenchEntry(reason))
      assert.deepEqual(notices, [workbenchEntryFailureMessage(reason)])
      assert.equal(failures(), 1)
      assert.doesNotMatch(notices[0], /已返回首页|JSON|IPC|Error|\.json/)
    })
  }
})
