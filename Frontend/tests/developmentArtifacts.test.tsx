import assert from 'node:assert/strict'
import './agentIntegration1008.test'
import { test } from 'node:test'
import './workbenchPhaseNavigation.test'
import './developmentNextStep.test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import {
  developmentArtifactTotals,
  developmentPlanningTotals,
  developmentCompletedCount,
  developmentStatusLabel,
  gateWorkbenchPhase,
  testEntryGateReason
} from '../src/renderer/src/developmentArtifacts'
import { WorkbenchPhaseProvider } from '../src/renderer/src/context/WorkbenchPhaseContext'
import { useWorkbenchPhase } from '../src/renderer/src/context/workbenchPhaseState'
import { latestApplicationLifecycle } from '../src/renderer/src/hooks/useApplicationLifecycleStore'
import DevelopmentStatusDot from '../src/renderer/src/components/AiChatPanel/components/ApplicationOutline/DevelopmentStatusDot'
import ApplicationOutline from '../src/renderer/src/components/AiChatPanel/components/ApplicationOutline'
import ApiOutlineGroup from '../src/renderer/src/components/AiChatPanel/components/ApplicationOutline/ApiOutlineGroup'
import TestPhaseConfirmationCard from '../src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/TestPhaseConfirmationCard'
import PlanExecutionDock from '../src/renderer/src/components/AiChatPanel/components/PlanExecutionDock'
import QuickTaskGuide from '../src/renderer/src/components/AiChatPanel/components/QuickTaskGuide'
import NextDevelopmentGuide from '../src/renderer/src/components/AiChatPanel/components/NextDevelopmentGuide'
import { developmentCompletedCount, developmentStatusText } from '../src/renderer/src/developmentArtifacts'
import type { ApplicationLifecycle, TestEntryGate } from '../src/renderer/src/typings'
import type { PlanExecutionMode } from '../src/renderer/src/components/AiChatPanel/planExecutionMode'

test('修订变更状态保留首次完成事实，选择卡和目录圆点展示有变更及待检查', () => {
  const changed = { initialDevelopmentStatus: 'completed' as const, changeStatus: 'changed' as const,
    changeReason: '接口参数变化', completedRunId: 'first' }
  const unknown = { ...changed, changeStatus: 'unavailable' as const }
  assert.equal(developmentStatusText(changed), '有变更')
  assert.equal(developmentStatusText(unknown), '待检查')
  assert.equal(developmentCompletedCount([changed, unknown, { initialDevelopmentStatus: 'completed' }]), 1)
  const dot = renderToStaticMarkup(createElement(DevelopmentStatusDot, { progress: changed }))
  assert.match(dot, /data-status="changed"/)
  assert.match(dot, /有变更：接口参数变化/)
  const html = renderToStaticMarkup(createElement(QuickTaskGuide, {
    developmentArtifacts: { pages: { orders: changed }, endpoints: {}, entities: {} },
    pages: [{ pageId: 'orders', label: '订单', path: '/orders', hasDetailPlan: true }],
    apiContracts: [], entities: [], disabled: false, loading: false, onStart: async () => undefined
  }))
  assert.match(html, /今天想从哪里开始/)
  assert.match(html, /有变更/)
})

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

test('新会话卡片只显示页面和接口，不提供实体设计入口', () => {
  const html = renderToStaticMarkup(
    <QuickTaskGuide
      pages={[
        {
          pageId: 'page',
          key: 'page',
          label: '页面',
          path: '/page',
          purpose: '录入',
          designed: true
        }
      ]}
      apiContracts={[
        {
          id: 'api',
          label: '接口',
          endpoints: [
            { id: 'get', name: '读取数据', method: 'GET', path: '/api', summary: '读取' }
          ]
        }
      ]}
      entities={[{ id: 'entity', label: '实体', purpose: '记录', dataSourceType: 'database' }]}
      developmentArtifacts={{
        pages: { page: { initialDevelopmentStatus: 'completed' } },
        endpoints: { api: { get: { initialDevelopmentStatus: 'in_progress' } } },
        entities: { entity: { initialDevelopmentStatus: 'pending' } }
      }}
      disabled={false}
      loading={false}
      onStart={async () => undefined}
    />
  )
  assert.match(html, /读取数据/)
  assert.match(html, />接口</)
  for (const [status, label] of [
    ['completed', '已初次完成'],
    ['in_progress', '开发中']
  ]) {
    assert.match(html, new RegExp(`data-status="${status}">${label}</span>`))
  }
  assert.doesNotMatch(html, /实体|data-status="pending"/)
})

test('API 检测通过后的选择卡要求具体页面或接口，支持空目标清单', () => {
  const html = renderToStaticMarkup(
    <NextDevelopmentGuide
      step={{ key: 'selection', reason: 'selection' }}
      tasks={[]}
      disabled={false}
      loading={false}
      onStart={async () => undefined}
    />
  )
  assert.match(html, /你想开发哪个页面或 API？/)
  assert.match(html, /API 配置检测已通过/)
  assert.match(html, /暂无可开发任务/)
  assert.match(html, /查看全部页面 \/ API/)
  assert.doesNotMatch(html, /本轮开发已完成|本轮开发已结束|quick-task-grid/)
  assert.doesNotMatch(html, /今天想从哪里开始|整个应用/)
})

test('开发产物接口树展示 Contract 和 Endpoint 名称并通过悬停提供路径', () => {
  const endpoint = {
    apiContractId: 'product-api',
    id: 'product-api.list',
    name: '查询商品列表',
    method: 'GET',
    path: '/api/product',
    summary: '查询商品'
  }
  const html = renderToStaticMarkup(
    <ApiOutlineGroup
      allEndpoints={[endpoint]}
      contract={{
        id: 'product-api',
        label: '/api/product',
        name: '商品管理',
        endpoints: [endpoint]
      }}
      expanded
      onSelect={() => undefined}
      onToggle={() => undefined}
      selectedKey=""
    />
  )

  assert.match(html, /商品管理/)
  assert.match(html, /商品管理，\/api\/product/)
  assert.match(html, /查询商品列表/)
  assert.match(html, /查询商品列表，GET \/api\/product/)
})

test('开发目录展示实体浏览入口并标记当前选中的实体', () => {
  const html = renderToStaticMarkup(
    <ApplicationOutline
      apiContracts={[]}
      entities={[{ id: 'Order', label: '订单实体', purpose: '规划定义', dataSourceType: 'database' }]}
      pages={[]}
      pageTree={[]}
      outlineLocked={false}
      selectedPageId=""
      selectedApiEndpointKey=""
      selectedEntityId="Order"
      onPageSelect={() => undefined}
      onApiEndpointSelect={() => undefined}
      onEntitySelect={() => assert.fail('渲染时不应触发实体选择')}
    />
  )
  assert.match(html, /页面|接口/)
  assert.match(html, /订单实体，Order/)
  assert.match(html, /aria-current="true"/)
})

test('完成比例只统计页面和 Endpoint，实体状态不影响阶段准入', () => {
  const artifacts = {
    pages: { page: { initialDevelopmentStatus: 'completed' as const } },
    endpoints: { api: { get: { initialDevelopmentStatus: 'completed' as const } } },
    entities: { Order: { initialDevelopmentStatus: 'pending' as const } }
  }
  assert.deepEqual(developmentArtifactTotals(artifacts), { completed: 2, total: 2 })
  assert.deepEqual(developmentPlanningTotals(
    [{ pageId: 'page', key: 'page', label: '页面', path: '/page', purpose: '', designed: false }],
    [{ id: 'api', label: '接口', endpoints: [{ id: 'get', method: 'GET', path: '/api', summary: '' }] }],
    artifacts
  ), { completed: 2, total: 2 })
  assert.deepEqual(developmentPlanningTotals([], [], artifacts), { completed: 0, total: 0 })
  artifacts.entities.Order.initialDevelopmentStatus = 'pending'
  assert.equal(gateWorkbenchPhase('test', { ...allowed, completed: 2, total: 2 }), 'test')
})

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
    testEntryGate: gate,
    extensions: {}
  }
}

/** 在真实 Provider 内读取自动阶段，验证门禁覆盖运行投影的测试阶段。 */
function CurrentPhase(): JSX.Element {
  return createElement('span', null, useWorkbenchPhase().phase)
}

/** 在实际阶段上下文中渲染确认卡，隔离服务端测试所需的本地存储。 */
function renderConfirmation(gate?: TestEntryGate, disabled = false, message?: string): string {
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
          message={message}
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

test('测试确认卡复用后端单测决策文案，并保留未完成产物的提示', () => {
  const skippedMessage =
    '代码生成与 Build 已完成，单元测试已跳过，确认后将进入测试阶段，执行测试与失败修复。'
  const skippedHtml = renderConfirmation(allowed, false, skippedMessage)
  assert.match(skippedHtml, /单元测试已跳过/)
  assert.doesNotMatch(skippedHtml, /单元测试门禁已完成/)
  assert.match(renderConfirmation(allowed), /单元测试门禁已完成/)
  const blockedHtml = renderConfirmation(blocked, false, skippedMessage)
  assert.match(blockedHtml, /完成全部开发产物后可进入测试/)
  assert.doesNotMatch(blockedHtml, /单元测试已跳过/)
})

test('门禁读取中或错误时显示原因且不暴露测试入口', () => {
  assert.match(renderConfirmation(), /正在读取开发产物状态/)
  const html = renderConfirmation({ ...blocked, blockers: [], reason: '产物目录读取失败' })
  assert.match(html, /产物目录读取失败/)
  assert.doesNotMatch(html, /测试目标|未完成产物|<button/)
})

/** 用真实底部控制栏检查单个 execution 的待确认状态与应用门禁是否一致。 */
function renderPhaseDock(
  mode: Exclude<PlanExecutionMode, 'idle'>,
  gate?: TestEntryGate,
  developmentTotals?: { completed: number; total: number }
): string {
  return renderToStaticMarkup(
    <PlanExecutionDock
      developmentTotals={developmentTotals}
      mode={mode}
      testEntryGate={gate}
      onAccept={async () => false}
      onConfirmInteraction={() => undefined}
      onEnd={() => undefined}
      onOpenPreview={() => undefined}
    />
  )
}

test('执行控制移除暂停和暂停续接，运行、停止及失败均可结束任务', () => {
  for (const mode of ['running', 'stopping', 'stopped', 'failed', 'awaiting_authorization', 'awaiting_repair_confirmation'] as const) {
    const html = renderPhaseDock(mode)
    assert.doesNotMatch(html, /暂停执行|正在暂停|继续执行/)
    assert.match(html, /结束任务/)
    assert.doesNotMatch(html, /<button[^>]*disabled[^>]*>结束任务/)
  }
})

test('底部阶段提示遵循全应用开发门禁，2/3 不声称可以进入测试或审查', () => {
  const partial: TestEntryGate = {
    ...blocked,
    completed: 2,
    pending: 1,
    inProgress: 0,
    blockers: [{ type: 'endpoint', apiContractId: 'api', endpointId: 'remaining' }],
    reason: '完成全部开发产物后可进入测试，当前 2/3。'
  }
  for (const mode of [
    'awaiting_test_phase_confirmation',
    'awaiting_review_phase_confirmation',
    'awaiting_acceptance_phase_confirmation'
  ] as const) {
    const html = renderPhaseDock(mode, partial)
    assert.match(html, /继续完成开发产物/)
    assert.equal(html.match(/2\/3/g)?.length, 1)
    assert.doesNotMatch(html, /等待进入测试阶段|等待进入审查阶段|等待进入验收阶段/)
    assert.doesNotMatch(html, /开发已完成|测试已通过|代码审查已完成/)
  }
  assert.match(renderPhaseDock('awaiting_test_phase_confirmation'), /正在读取开发产物状态/)
  assert.match(renderPhaseDock('awaiting_test_phase_confirmation', allowed), /等待进入测试阶段/)
  assert.match(renderPhaseDock('awaiting_review_phase_confirmation', allowed), /等待进入审查阶段/)
  for (const mode of [
    'awaiting_test_phase_confirmation',
    'awaiting_review_phase_confirmation'
  ] as const) {
    const unsynced = renderPhaseDock(mode, allowed, { completed: 2, total: 3 })
    assert.equal(unsynced.match(/2\/3/g)?.length, 1)
    assert.doesNotMatch(unsynced, /等待进入测试阶段|等待进入审查阶段|开发已完成/)
  }
})

test('未加载和未完成时禁止测试及后续阶段；完成后允许', () => {
  assert.equal(gateWorkbenchPhase('test'), 'development')
  assert.equal(gateWorkbenchPhase('test', blocked), 'development')
  assert.equal(gateWorkbenchPhase('test', allowed), 'test')
  assert.equal(gateWorkbenchPhase('review', blocked), 'development')
  assert.equal(gateWorkbenchPhase('acceptance', blocked), 'development')
  assert.equal(gateWorkbenchPhase('review', allowed), 'review')
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
