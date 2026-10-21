import { mergeEndedTaskAvailability } from '../src/renderer/src/service/endedTaskAvailability'
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { renderToStaticMarkup } from 'react-dom/server'
import type { ApplicationLifecycle, WorkflowRunPayload } from '../src/renderer/src/typings'
import type { AgentChatMessage } from '../src/renderer/src/components/AiChatPanel/types'
import {
  developmentNextStep,
  latestDevelopmentTarget,
  nextDevelopmentTask
} from '../src/renderer/src/components/AiChatPanel/developmentNextStep'
import {
  allDevelopmentTargetsCompleted,
  filterDevelopmentTargets
} from '../src/renderer/src/components/AiChatPanel/components/NextDevelopmentGuide/pickerTasks'
import DevelopmentTargetPicker from '../src/renderer/src/components/AiChatPanel/components/NextDevelopmentGuide/DevelopmentTargetPicker'
import NextDevelopmentGuide from '../src/renderer/src/components/AiChatPanel/components/NextDevelopmentGuide'
import { buildQuickTasks } from '../src/renderer/src/components/AiChatPanel/components/QuickTaskGuide/quickTasks'

test('技术规划停止生成的结束回执不展示开发工作区检查卡', () => {
  const ended: WorkflowRunPayload = {
    runId: 'technical-run', threadId: 'planning-thread', events: [],
    state: { workflow_scope: 'application_planning' },
    summary: { status: 'completed', phase: 'plan_control',
      planControl: { action: 'end', status: 'ended', targetRunId: 'technical-run' } }
  }
  assert.equal(developmentNextStep([message(completion()), message(ended)]), undefined)
  const currentLifecycle = { initialization: { threadId: 'planning-thread' }, endedExecutions: {} } as ApplicationLifecycle
  // 现场已显示的回执即便只有 Run/Thread，也能由当前规划归属判定，不能永远等待不存在的归档。
  assert.equal(developmentNextStep([message({ ...ended, state: undefined })], currentLifecycle), undefined)
})

test('原开发 Run 已结束后，同 Thread 新测试失败不显示重新选择开发任务', () => {
  const failed = { runId: 'test-run', threadId: 'shared-thread', events: [],
    summary: { status: 'failed', phase: 'integration_test' } } as WorkflowRunPayload
  const ended = { execution: { runId: 'old-build', threadId: 'shared-thread', status: 'stopped' },
    workspaceUnchanged: false }
  const lifecycle = { endedExecutions: { 'old-build': ended } } as unknown as ApplicationLifecycle
  assert.equal(developmentNextStep([message(failed)], lifecycle), undefined)
  const sameRun = { ...lifecycle, endedExecutions: { 'test-run': {
    ...ended, execution: { ...ended.execution, runId: 'test-run' }
  } } } as ApplicationLifecycle
  assert.equal(developmentNextStep([message(failed)], sameRun)?.endedRunId, 'test-run')
})

test('继续已结束任务的启动失败时，只要原 Thread 仍有停止归档就重建继续入口', () => {
  const failedContinue = {
    runId: 'continue-attempt',
    threadId: 'conversation-thread',
    events: [],
    state: { workflow_scope: 'conversation' },
    summary: { status: 'failed', phase: 'conversation' }
  } as WorkflowRunPayload
  const archived = {
    execution: {
      runId: 'paused-conversation',
      threadId: 'conversation-thread',
      scope: 'application',
      targetId: 'conversation',
      status: 'stopped',
      updatedAt: '2026-10-20T00:00:00Z'
    },
    workspaceUnchanged: true
  }
  const lifecycle = {
    endedExecutions: { 'paused-conversation': archived }
  } as unknown as ApplicationLifecycle
  const userMessage: AgentChatMessage = {
    id: 2,
    role: 'user',
    content: '继续已结束的任务。'
  }
  const step = developmentNextStep(
    [userMessage, message(failedContinue)],
    lifecycle
  )
  assert.equal(step?.endedRunId, 'paused-conversation')
  assert.equal(step?.workspaceUnchanged, true)
  assert.equal(
    developmentNextStep(
      [userMessage, message({ ...failedContinue, threadId: 'other-thread' })],
      lifecycle
    ),
    undefined
  )
})

/** 构造服务端已经确认当前页初次完成的真实结构。 */
function completion(): WorkflowRunPayload {
  return {
    runId: 'run-one',
    threadId: 'graph-one',
    events: [],
    summary: {
      status: 'requires_user_input',
      phase: 'test_phase_confirmation',
      buildExecutionScope: { type: 'page', targetId: 'one' },
      testTarget: { type: 'page', id: 'one', label: '介绍页' }
    }
  }
}
/** 只包装结构化 Workflow，避免测试借用消息自然语言判断完成。 */
function message(workflow: WorkflowRunPayload): AgentChatMessage {
  return { id: 1, role: 'assistant', content: '当前步骤执行完成', workflow }
}
const lifecycle = {
  developmentArtifacts: {
    pages: {
      one: {
        initialDevelopmentStatus: 'completed',
        completedRunId: 'run-one',
        completedThreadId: 'graph-one'
      },
      two: { initialDevelopmentStatus: 'pending' },
      busy: { initialDevelopmentStatus: 'in_progress' }
    },
    endpoints: { api: { get: { initialDevelopmentStatus: 'pending' } } }
  }
} as ApplicationLifecycle
const tasks = buildQuickTasks(
  [
    { pageId: 'one', key: 'one', label: '介绍页', path: '/one', purpose: '', designed: true },
    { pageId: 'two', key: 'two', label: '我的回检', path: '/two', purpose: '', designed: true },
    {
      pageId: 'busy',
      key: 'busy',
      label: '开发中的页面',
      path: '/busy',
      purpose: '',
      designed: true
    }
  ],
  [
    {
      id: 'api',
      label: '回检',
      endpoints: [{ id: 'get', name: '查询回检', method: 'GET', path: '/api', summary: '' }]
    }
  ],
  [],
  lifecycle.developmentArtifacts
)

test('只有本次正式运行的初次完成事实触发引导，普通节点 completed 不触发', () => {
  assert.equal(developmentNextStep([message(completion())], lifecycle)?.reason, 'completed')
  for (const [phase, status] of [
    ['build', 'completed'],
    ['unit_test', 'completed'],
    ['test_phase_confirmation', 'stopped'],
    ['test_phase_confirmation', 'failed'],
    ['test_phase_confirmation', 'running']
  ]) {
    const workflow = completion()
    Object.assign(workflow.summary, { phase, status })
    assert.equal(developmentNextStep([message(workflow)], lifecycle), undefined)
  }
  const otherCompletion = completion()
  otherCompletion.runId = 'another-run'
  otherCompletion.threadId = 'another-thread'
  assert.equal(developmentNextStep([message(otherCompletion)], lifecycle), undefined)
  assert.equal(developmentNextStep([message(completion())]), undefined)
})

test('End 成功才出现结束引导，保留未完成目标状态，不把 stop 或失败 End 算成结束', () => {
  const source = completion()
  source.summary.buildExecutionScope = { type: 'page', targetId: 'two' }
  source.summary.status = 'stopped'
  const control: WorkflowRunPayload = {
    runId: 'control',
    threadId: 'visible-thread',
    events: [],
    summary: {
      phase: 'plan_control',
      status: 'completed',
      planControl: { action: 'end', status: 'ended', targetRunId: source.runId }
    }
  }
  const step = developmentNextStep([message(source), message(control)], lifecycle)
  assert.equal(step?.reason, 'ended')
  assert.equal(step?.target?.type === 'page' && step.target.pageId, 'two')
  assert.equal(lifecycle.developmentArtifacts?.pages.two.initialDevelopmentStatus, 'pending')
  control.summary.status = 'failed'
  assert.equal(developmentNextStep([message(source), message(control)], lifecycle), undefined)
  control.summary.status = 'completed'
  control.summary.planControl = { action: 'stop', status: 'stopped', targetRunId: source.runId }
  assert.equal(developmentNextStep([message(source), message(control)], lifecycle), undefined)
})

test('新任务取代上一张引导，推荐跳过当前目标、已完成和开发中目标', () => {
  const first = message(completion())
  assert.equal(
    nextDevelopmentTask(tasks, developmentNextStep([first], lifecycle)?.target)?.id,
    'page:two'
  )
  const next = completion()
  next.runId = 'run-two'
  next.summary = {
    status: 'requires_user_input',
    phase: 'api_design_readiness_gate',
    buildExecutionScope: { type: 'page', targetId: 'two' }
  }
  assert.equal(developmentNextStep([first, message(next)], lifecycle), undefined)
  assert.equal(
    latestDevelopmentTarget([first, message(next)])?.type === 'page' &&
      (latestDevelopmentTarget([first, message(next)]) as { pageId: string }).pageId,
    'two'
  )
  assert.equal(
    nextDevelopmentTask(tasks.filter((task) => task.kind !== 'page'))?.id,
    'endpoint:api:get'
  )
})

test('显式放弃规划成功后展示结束引导，无草稿和过期拒绝不展示', () => {
  const control: WorkflowRunPayload = {
    runId: 'abandon',
    threadId: 'thread',
    events: [],
    summary: { status: 'completed', phase: 'build_task_plan_abandon' },
    state: { buildTaskPlanAbandon: { status: 'abandoned', planningRunId: 'planning' } }
  }
  assert.equal(developmentNextStep([message(control)], lifecycle)?.reason, 'ended')
  for (const status of ['no_pending', 'stale_draft', 'already_confirmed']) {
    control.state!.buildTaskPlanAbandon = { status }
    assert.equal(developmentNextStep([message(control)], lifecycle), undefined)
  }
})

test('完成后的闲聊不消费引导，新的修改确认、失败或暂停不回显旧卡', () => {
  const conversation: WorkflowRunPayload = {
    runId: 'chat',
    threadId: 'chat-thread',
    events: [],
    summary: { phase: 'conversation', status: 'completed', intent: 'casual_chat' }
  }
  assert.equal(
    developmentNextStep([message(completion()), message(conversation)], lifecycle)?.reason,
    'completed'
  )
  for (const status of ['requires_user_input', 'failed', 'stopped']) {
    conversation.summary.status = status
    assert.equal(
      developmentNextStep([message(completion()), message(conversation)], lifecycle),
      undefined
    )
  }
})

test('API 检查后复用会话末尾卡片并保留检查结果与推荐入口', () => {
  const html = renderToStaticMarkup(
    <NextDevelopmentGuide
      step={{ key: 'selection', reason: 'selection', message: '已检查 1 个已完成页面/API，1 个有变更。' }}
      tasks={tasks}
      disabled={false}
      loading={false}
      onStart={async () => undefined}
    />
  )
  assert.match(html, /继续开发引导/)
  assert.match(html, /已检查 1 个已完成页面\/API，1 个有变更/)
  assert.match(html, /推荐下一项/)
  assert.match(html, /查看全部页面 \/ API/)
  assert.doesNotMatch(html, /本轮开发已完成|本轮开发已结束|quick-task-grid/)
})

test('全部完成显示暂无可开发任务，目录错误和锁定仍禁止发起新任务', () => {
  const step = developmentNextStep([message(completion())], lifecycle)!
  const html = renderToStaticMarkup(
    <NextDevelopmentGuide
      step={step}
      tasks={tasks.slice(0, 1)}
      disabled={false}
      loading={false}
      onStart={async () => undefined}
    />
  )
  assert.match(html, /暂无可开发任务/)
  assert.doesNotMatch(html, /aria-label="开发「/)
  const locked = renderToStaticMarkup(
    <NextDevelopmentGuide
      step={step}
      tasks={tasks}
      disabled
      disabledHint="请先处理待确认计划"
      loading={false}
      onStart={async () => undefined}
    />
  )
  assert.match(locked, /disabled=""/)
  assert.match(locked, /请先处理待确认计划/)
  const failed = renderToStaticMarkup(
    <NextDevelopmentGuide
      step={step}
      tasks={tasks}
      disabled={false}
      catalogError="目录读取失败"
      loading={false}
      onStart={async () => undefined}
    />
  )
  assert.match(failed, /目录读取失败/)
  assert.doesNotMatch(failed, /aria-label="开发「/)
})

test('卡片区分完成与主动结束，推荐操作可见且没有自动提交', () => {
  const completed = developmentNextStep([message(completion())], lifecycle)!
  const html = renderToStaticMarkup(
    <NextDevelopmentGuide
      step={completed}
      tasks={tasks}
      disabled={false}
      loading={false}
      onStart={async () => assert.fail('渲染不能发起开发')}
    />
  )
  assert.match(html, /介绍页初次开发已完成/)
  assert.match(html, /开发「我的回检」/)
  assert.match(html, /查看全部页面 \/ API/)
  const endedHtml = renderToStaticMarkup(
    <NextDevelopmentGuide
      step={{
        ...completed,
        reason: 'ended',
        target: { type: 'page', pageId: 'two', label: '我的回检' }
      }}
      tasks={tasks}
      disabled
      loading={false}
      onStart={async () => undefined}
    />
  )
  assert.match(endedHtml, /本次开发已结束/)
  assert.match(endedHtml, /2 个页面/)
  assert.doesNotMatch(endedHtml, /我的回检初次开发已完成/)
})

test('完成判断覆盖页面和 API，未加载、空目录、目录错误和未知进度不误判', () => {
  assert.equal(allDevelopmentTargetsCompleted(tasks, false), false)
  assert.equal(allDevelopmentTargetsCompleted(tasks.slice(0, 1), false), true)
  assert.equal(allDevelopmentTargetsCompleted(tasks.slice(0, 1), true), false)
  assert.equal(allDevelopmentTargetsCompleted(tasks.slice(0, 1), false, '目录错误'), false)
  assert.equal(allDevelopmentTargetsCompleted([], false), false)
  const empty = renderToStaticMarkup(
    <NextDevelopmentGuide step={{ key: 'end', reason: 'ended' }} tasks={[]} disabled={false}
      loading={false} onStart={async () => undefined} />
  )
  assert.match(empty, /暂无可开发任务/)
  assert.doesNotMatch(empty, /全部开发完成|全部页面和 API 已完成/)
  assert.equal(allDevelopmentTargetsCompleted([{ ...tasks[0], progress: undefined }], false), false)
  const finished = tasks.map((task) => ({
    ...task,
    progress: { initialDevelopmentStatus: 'completed' as const }
  }))
  assert.equal(allDevelopmentTargetsCompleted(finished, false), true)
  finished[finished.length - 1] = tasks[tasks.length - 1] as (typeof finished)[number]
  assert.equal(allDevelopmentTargetsCompleted(finished, false), false)
})

test('完整目录支持状态类型搜索联合筛选，并且只给未开发目标提供启动操作', () => {
  assert.equal(filterDevelopmentTargets(tasks, 'all', 'all', '').length, 4)
  assert.deepEqual(
    filterDevelopmentTargets(tasks, 'pending', 'endpoint', '/API').map((task) => task.id),
    ['endpoint:api:get']
  )
  assert.equal(filterDevelopmentTargets(tasks, 'completed', 'page', '').length, 1)
  assert.equal(filterDevelopmentTargets(tasks, 'in_progress', 'all', '').length, 1)
  assert.equal(filterDevelopmentTargets(tasks, 'pending', 'page', '没有这个目标').length, 0)
  const html = renderToStaticMarkup(
    <DevelopmentTargetPicker
      tasks={tasks}
      blocked={false}
      startingId=""
      onStart={async () => assert.fail('筛选不能发起开发')}
    />
  )
  assert.match(html, /全部页面与 API/)
  assert.match(html, /按开发状态筛选/)
  assert.match(html, /开始开发「我的回检」/)
  assert.doesNotMatch(html, /开始开发「介绍页」|开始开发「开发中的页面」|data-recommended/)
})


test('结束后工作区未变化仅提供继续，变化后仅提供重新发起入口', () => {
  for (const unchanged of [true, false]) {
    const html = renderToStaticMarkup(<NextDevelopmentGuide
      step={{ key: 'ended', reason: 'ended', endedRunId: 'source', workspaceUnchanged: unchanged }}
      tasks={tasks} disabled={false} loading={false} onStart={async () => undefined}
      onContinue={async () => undefined} />)
    assert.match(html, unchanged ? /继续任务/ : /重新发起任务/)
    assert.doesNotMatch(html, unchanged ? /重新发起任务/ : />继续任务</)
    assert.doesNotMatch(html, /查看全部页面|推荐下一项|aria-label="开发「/)
  }
})


test('结束资格同 revision 更新，迟到或旧 revision 的检查不能回退当前结果', () => {
  const current = { ...lifecycle, endedExecutionsCheckedAt: '2026-10-15T01:00:00Z' }
  const incoming = { ...current, endedExecutions: {}, endedExecutionsCheckedAt: '2026-10-15T01:00:03Z' }
  const latest = mergeEndedTaskAvailability(current, current, incoming)
  assert.equal(latest.endedExecutionsCheckedAt, incoming.endedExecutionsCheckedAt)
  assert.equal(mergeEndedTaskAvailability(latest, latest, current), latest)
  assert.equal(mergeEndedTaskAvailability(latest, latest, { ...incoming, revision: current.revision - 1 }), latest)
})
