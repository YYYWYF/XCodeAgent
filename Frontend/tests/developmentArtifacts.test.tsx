import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import {
  developmentCompletedCount,
  developmentStatusLabel,
  gateWorkbenchPhase,
  testEntryGateReason
} from '../src/renderer/src/developmentArtifacts'
import { WorkbenchPhaseProvider } from '../src/renderer/src/context/WorkbenchPhaseContext'
import { useWorkbenchPhase } from '../src/renderer/src/context/workbenchPhaseState'
import { latestApplicationLifecycle } from '../src/renderer/src/hooks/useApplicationLifecycleStore'
import DevelopmentStatusDot from '../src/renderer/src/components/AiChatPanel/components/ApplicationOutline/DevelopmentStatusDot'
import TestPhaseConfirmationCard from '../src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/TestPhaseConfirmationCard'
import type { ApplicationLifecycle, TestEntryGate } from '../src/renderer/src/typings'

const blocked: TestEntryGate = {
  allowed: false,
  total: 3,
  completed: 1,
  pending: 1,
  inProgress: 1,
  blockers: [
    { type: 'page', pageId: 'two' },
    { type: 'endpoint', apiContractId: 'api', endpointId: 'get' }
  ],
  reason: '完成全部开发产物后可进入测试，当前 1/3。'
}
const allowed: TestEntryGate = {
  ...blocked,
  allowed: true,
  completed: 3,
  pending: 0,
  inProgress: 0,
  blockers: [],
  reason: null
}

/** 构造实际阶段 Provider 和 revision 合并测试所需的生命周期快照。 */
function lifecycle(gate?: TestEntryGate, revision = 1): ApplicationLifecycle {
  return {
    application: { id: 'test', name: '测试应用' },
    updatedAt: '2026-09-08T00:00:00Z',
    revision,
    initialization: { stage: 'ready_for_workbench', status: 'completed' },
    activeExecutions: {
      test: {
        scope: 'page',
        targetId: 'one',
        threadId: 'thread',
        runId: 'run',
        phase: 'integration_test',
        status: 'running',
        startedAt: '',
        updatedAt: ''
      }
    },
    testEntryGate: gate
  }
}

/** 在真实 Provider 内读取自动阶段，验证门禁覆盖运行投影的测试阶段。 */
function CurrentPhase(): JSX.Element {
  return createElement('span', null, useWorkbenchPhase().phase)
}

/** 在实际阶段上下文中渲染确认卡，隔离服务端测试所需的本地存储。 */
function renderConfirmation(gate?: TestEntryGate, disabled = false): string {
  const previousWindow = Object.getOwnPropertyDescriptor(globalThis, 'window')
  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: { localStorage: { getItem: () => null } }
  })
  try {
    return renderToStaticMarkup(
      <WorkbenchPhaseProvider applicationId="test" lifecycle={lifecycle(gate)}>
        <TestPhaseConfirmationCard
          disabled={disabled}
          target={{ type: 'endpoint', id: 'age', label: 'POST /api/age' }}
          onSubmit={() => assert.fail('渲染卡片不应启动测试')}
        />
      </WorkbenchPhaseProvider>
    )
  } finally {
    if (previousWindow) Object.defineProperty(globalThis, 'window', previousWindow)
    else Reflect.deleteProperty(globalThis, 'window')
  }
}

test('未全部完成时只逐项展示剩余产物，隐藏测试目标和测试按钮', () => {
  const html = renderConfirmation(blocked)
  assert.match(html, /当前产物初次开发已完成/)
  assert.match(html, /未完成产物/)
  assert.match(html, /2 项/)
  assert.match(html, /页面/)
  assert.match(html, /接口/)
  assert.match(html, /two/)
  assert.match(html, /api\/get/)
  assert.equal((html.match(/<li>/g) || []).length, 2)
  assert.doesNotMatch(html, /测试目标|POST \/api\/age|<button/)
})

test('全量门禁解锁后恢复测试目标和入口，提交期间保留禁用行为', () => {
  const html = renderConfirmation(allowed)
  assert.match(html, /测试目标/)
  assert.match(html, /POST \/api\/age/)
  assert.match(html, /<button/)
  assert.doesNotMatch(html, /未完成产物|disabled=""/)
  assert.match(renderConfirmation(allowed, true), /disabled=""/)
})

test('门禁读取中或错误时显示原因且不暴露测试入口', () => {
  assert.match(renderConfirmation(), /正在读取开发产物状态/)
  const html = renderConfirmation({ ...blocked, blockers: [], reason: '产物目录读取失败' })
  assert.match(html, /产物目录读取失败/)
  assert.doesNotMatch(html, /测试目标|未完成产物|<button/)
})

test('未加载和未完成时禁止测试；完成后允许；其他阶段沿用现有规则', () => {
  assert.equal(gateWorkbenchPhase('test'), 'development')
  assert.equal(gateWorkbenchPhase('test', blocked), 'development')
  assert.equal(gateWorkbenchPhase('test', allowed), 'test')
  assert.equal(gateWorkbenchPhase('review', blocked), 'review')
  assert.equal(gateWorkbenchPhase('product', blocked), 'product')
  assert.equal(testEntryGateReason(blocked), blocked.reason)
  assert.match(testEntryGateReason(), /正在读取/)
})

test('真实阶段 Provider 在冷启动和运行投影时也检查门禁', () => {
  const previousWindow = Object.getOwnPropertyDescriptor(globalThis, 'window')
  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: {
      localStorage: { getItem: () => 'test' }
    }
  })
  try {
    for (const [gate, expected] of [
      [undefined, 'development'],
      [blocked, 'development'],
      [allowed, 'test']
    ] as const) {
      const html = renderToStaticMarkup(
        <WorkbenchPhaseProvider applicationId="test" lifecycle={lifecycle(gate)}>
          <CurrentPhase />
        </WorkbenchPhaseProvider>
      )
      assert.equal(html, `<span>${expected}</span>`)
    }
  } finally {
    if (previousWindow) Object.defineProperty(globalThis, 'window', previousWindow)
    else Reflect.deleteProperty(globalThis, 'window')
  }
})

test('初次完成计数不把进行中或缺失项计入绿色', () => {
  assert.equal(
    developmentCompletedCount([
      { initialDevelopmentStatus: 'completed' },
      { initialDevelopmentStatus: 'pending' },
      { initialDevelopmentStatus: 'in_progress' },
      undefined
    ]),
    1
  )
})

test('圆点实际渲染三态和可访问文字说明', () => {
  for (const status of ['pending', 'in_progress', 'completed'] as const) {
    const progress = { initialDevelopmentStatus: status }
    const html = renderToStaticMarkup(createElement(DevelopmentStatusDot, { progress }))
    assert.match(html, new RegExp(`data-status="${status}"`))
    assert.ok(html.includes(`aria-label="${developmentStatusLabel(progress)}"`))
    assert.match(html, /role="img"/)
  }
})

test('旧冷启动快照不能覆盖实时完成门禁，新增产物快照可以重新关闭入口', () => {
  const completed = lifecycle(allowed, 3)
  assert.equal(latestApplicationLifecycle(completed, lifecycle(blocked, 2)), completed)
  const added = lifecycle(blocked, 4)
  assert.equal(latestApplicationLifecycle(completed, added), added)
  assert.equal(gateWorkbenchPhase('test', added.testEntryGate), 'development')
})
