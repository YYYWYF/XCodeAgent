import assert from 'node:assert/strict'
import {
  ApplicationPlanningRuntime,
  type ApplicationPlanningRuntimeDependencies
} from '../src/renderer/src/service/applicationPlanningRuntime'
import {
  reduceApplicationPlanningCurrentState,
  type ApplicationPlanningCurrentEvent,
  type ApplicationPlanningCurrentState
} from '../src/renderer/src/service/activeApplicationPlanning'
import { AgUiRunError, type AgUiChatResult, type SendWorkflowMessageOptions } from '../src/renderer/src/service/agUiAgent'
import {
  ApplicationPlanningCheckpointNotFoundError,
  type ApplicationPlanningRecoveryProjection
} from '../src/renderer/src/service/applicationPlanningRecovery'
import type {
  ApplicationConfig,
  ApplicationLifecycle,
  WorkflowDesignStageRevisionStart,
  WorkflowRunPayload
} from '../src/renderer/src/typings'

/** 构造带稳定身份的最小 Planning 当前状态，不挂载任何 React 视图。 */
function planningState(applicationId = 'app-A', threadId = 'thread-A'): ApplicationPlanningCurrentState {
  const application = { id: applicationId, appName: applicationId, workspaceRoot: `/workspace/${applicationId}` } as ApplicationConfig
  return {
    application, threadId, transportState: 'idle',
    lifecycle: {
      application: { id: applicationId, name: applicationId }, revision: 1, updatedAt: '2026-09-10T00:00:00Z',
      initialization: { threadId, stage: 'generating_requirement_document', status: 'running' },
      activeExecutions: {}, extensions: {}
    }
  }
}

/** 构造当前线程的技术规划确认快照，业务状态明确保持待输入。 */
function confirmationWorkflow(threadId = 'thread-A', gateId = 'current-gate'): WorkflowRunPayload {
  return {
    runId: 'run-A', threadId, events: [],
    summary: { status: 'requires_user_input', phase: 'technical_planning', clarification: { mode: 'technical_plan_confirmation', status: 'requires_user_input' } },
    state: { application_planning_interrupt: { gateId, artifact: 'technical_plan', artifactRevision: 'revision-current' } },
    result: {}
  } as WorkflowRunPayload
}

/** 构造同线程但缺少服务端中断的历史快照，用于覆盖提交前恢复。 */
function workflowWithoutInterrupt(threadId = 'thread-A'): WorkflowRunPayload {
  return {
    ...confirmationWorkflow(threadId),
    state: {},
    result: {}
  }
}

/** 构造 AG-UI 会话返回值，允许同一次调用先发帧再返回或失败。 */
function result(workflow?: WorkflowRunPayload): AgUiChatResult {
  return { threadId: workflow?.threadId || 'thread-A', runId: 'run-A', answer: '', workflow, toolCalls: [], processSteps: [] }
}

/** 构造同应用的权威 lifecycle，并允许测试覆盖阶段、状态和 revision。 */
function authoritativeLifecycle(
  current: ApplicationPlanningCurrentState,
  initialization: Partial<ApplicationLifecycle['initialization']> = {},
  revision = current.lifecycle.revision + 1
): ApplicationLifecycle {
  return {
    ...current.lifecycle,
    revision,
    initialization: { ...current.lifecycle.initialization, ...initialization }
  }
}

/** 构造 Backend 权威恢复分类，默认表示真实 Native Interrupt 仍在等待用户。 */
function recoveryProjection(
  threadId = 'thread-A',
  overrides: Partial<ApplicationPlanningRecoveryProjection> = {}
): ApplicationPlanningRecoveryProjection {
  return {
    schemaVersion: 'application-planning-recovery.v1',
    classification: 'awaiting_user',
    sourceRunId: 'run-A',
    threadId,
    canContinue: false,
    userActionRequired: true,
    inputCommitted: false,
    reasonCode: 'NATIVE_APPLICATION_PLANNING_INTERRUPT',
    message: '当前应用规划正在等待你的确认。',
    ...overrides
  }
}

/** 注入可控会话与真实 Canonical reducer，记录所有外部调用和到达顺序。 */
function harness(initial = planningState(), overrides: Partial<ApplicationPlanningRuntimeDependencies> = {}) {
  let current: ApplicationPlanningCurrentState | undefined = initial
  let active = false
  let stopCalls = 0
  let stop = async (): Promise<void> => { active = false }
  let send: (options: SendWorkflowMessageOptions) => Promise<AgUiChatResult> = async () => result()
  let read = async () => ({
    workflow: confirmationWorkflow(initial.threadId),
    lifecycle: authoritativeLifecycle(initial, {
      stage: 'awaiting_technical_plan_confirmation',
      status: 'awaiting_user'
    }),
    recovery: recoveryProjection(initial.threadId)
  })
  let readCalls = 0
  const events: ApplicationPlanningCurrentEvent[] = []
  const calls: { message: string; options: SendWorkflowMessageOptions }[] = []
  const published: WorkflowRunPayload[] = []
  const contents: string[] = []
  const order: string[] = []
  const runtime = new ApplicationPlanningRuntime({
    applicationId: initial.application.id, threadId: initial.threadId,
    /** 测试中保持与生产相同的同步 Canonical 读取。 */
    getCurrentState: () => current,
    /** 先记录并提交事件，再允许 Runtime 读取合并结果。 */
    dispatchCurrentEvent: (event) => {
      events.push(event)
      order.push(event.type)
      if (current) current = reduceApplicationPlanningCurrentState(current, event)
    },
    /** 记录进入聊天历史的内容。 */
    publishContent: (content) => { contents.push(content) },
    /** 记录发布时已经可读的 Canonical 快照。 */
    publishWorkflow: (workflow) => {
      assert.equal(current?.workflow, workflow)
      order.push('publish_workflow')
      published.push(workflow)
    },
    /** 默认不进入真实模板服务。 */
    onTechnicalPlanConfirmed: async () => true,
    /** 默认不进入真实工作台交接。 */
    onRevisionContinuation: async () => {},
    /** 默认返回同一应用和 thread 的权威确认快照。 */
    readAuthoritativeSnapshot: async () => {
      readCalls += 1
      return read()
    },
    session: {
      /** 记录标准 AG-UI options 并模拟传输是否活动。 */
      sendMessage: async (message, options) => {
        calls.push({ message, options })
        active = true
        try { return await send(options) } finally { active = false }
      },
      /** 停止只影响本测试实例的会话。 */
      stop: async () => { stopCalls += 1; await stop() },
      /** 读取当前模拟传输活动状态。 */
      hasActiveRun: () => active
    },
    ...overrides
  })
  return {
    runtime, events, calls, published, contents, order,
    /** 读取测试 Canonical 事实。 */
    current: () => current,
    /** 模拟权威 store 更新或删除规划。 */
    setCurrent: (next: ApplicationPlanningCurrentState | undefined) => { current = next },
    /** 设置下一轮 AG-UI 响应行为。 */
    onSend: (handler: typeof send) => { send = handler },
    /** 设置下一轮独立权威读取行为。 */
    onRead: (handler: typeof read) => { read = handler },
    /** 设置下一轮 stop 行为。 */
    onStop: (handler: typeof stop) => { stop = handler },
    /** 查询独立权威读取次数。 */
    readCalls: () => readCalls,
    /** 查询本实例 stop 调用次数。 */
    stopCalls: () => stopCalls
  }
}

/** 等待异步调用链到达可观察条件，避免测试依赖固定 microtask 次数。 */
async function waitForCondition<T>(
  read: () => T | undefined,
  description: string,
  timeoutMs = 5_000
): Promise<T> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() <= deadline) {
    const value = read()
    if (value !== undefined) return value
    await new Promise((resolve) => setTimeout(resolve, 10))
  }
  throw new Error(`等待${description}超时。`)
}

// A：没有 Modal 也会启动；重复 ensureStarted 不创建第二轮执行。
{
  const h = harness()
  await h.runtime.ensureStarted()
  await h.runtime.ensureStarted()
  assert.equal(h.calls.length, 1)
  assert.equal(h.events[0].type, 'run_started')
  assert.equal(h.calls[0].options.workflowScope, 'application_planning')
  assert.equal(h.current()?.transportState, 'idle')
}

// B：awaiting_user 使用独立只读 client 恢复，不占用主 Planning Session。
{
  const current = planningState()
  current.lifecycle.initialization.status = 'awaiting_user'
  const h = harness(current)
  await h.runtime.ensureStarted()
  assert.equal(h.readCalls(), 1)
  assert.equal(h.calls.length, 0)
  assert.deepEqual(h.contents, [])
  assert.equal(h.current()?.workflow?.summary.status, 'requires_user_input')
}

// C：确认帧后 transport 中断会权威收敛，不伪造成业务失败。
{
  const h = harness()
  h.onSend(async (options) => {
    options.onWorkflow?.(confirmationWorkflow())
    assert.equal(h.current()?.workflow?.summary.status, 'requires_user_input')
    throw new Error('transport interrupted')
  })
  await h.runtime.ensureStarted()
  assert.equal(h.readCalls(), 1)
  assert.equal(h.current()?.workflow?.summary.clarification?.mode, 'technical_plan_confirmation')
  assert.equal(h.current()?.error, undefined)
  assert.equal(h.current()?.syncError, undefined)
  assert.equal(h.current()?.transportState, 'idle')
}

// D：Runtime 创建后更新生命周期，重试必须使用调用瞬间的恢复节点。
{
  const h = harness()
  const current = h.current()!
  h.setCurrent({ ...current, lifecycle: { ...current.lifecycle, initialization: { ...current.lifecycle.initialization, stage: 'generating_technical_plan', status: 'failed' } } })
  await h.runtime.retryCurrentFailure()
  assert.equal(h.calls[0].options.workflowDebug?.resumeFrom, 'technical_planning')
}

// E：两个应用各自持有会话、事件和流式订阅。
{
  const a = harness()
  const b = harness(planningState('app-B', 'thread-B'))
  a.onSend(async (options) => {
    options.onContent?.('A 的正文')
    options.onWorkflow?.(confirmationWorkflow('thread-B'))
    return result(confirmationWorkflow())
  })
  await a.runtime.ensureStarted()
  assert.equal(b.calls.length, 0)
  assert.equal(b.events.length, 0)
  assert.deepEqual(b.contents, [])
  assert.ok(a.events.every((event) => event.applicationId === 'app-A' && event.threadId === 'thread-A'))
  assert.equal(a.events.filter((event) => event.type === 'workflow_received').length, 1)
}

// F：销毁后晚到内容、workflow 和最终返回值都不能再写状态或发布历史。
{
  const h = harness()
  let options: SendWorkflowMessageOptions | undefined
  let finish!: (value: AgUiChatResult) => void
  h.onSend((incoming) => {
    options = incoming
    return new Promise<AgUiChatResult>((resolve) => { finish = resolve })
  })
  const running = h.runtime.ensureStarted()
  h.runtime.dispose()
  const eventCount = h.events.length
  options?.onWorkflow?.(confirmationWorkflow())
  options?.onContent?.('晚到内容')
  finish(result(confirmationWorkflow()))
  await running
  assert.equal(h.events.length, eventCount)
  assert.deepEqual(h.published, [])
  assert.deepEqual(h.contents, [])
  assert.equal(h.stopCalls(), 1)
}

// G：保存服务完成后合并最新 Canonical Workflow，再把 artifact 返回给调用方。
{
  const initial = planningState()
  initial.workflow = confirmationWorkflow()
  const saved = {
    artifact: { id: 'requirement_spec' as const, name: '需求文档', path: 'specs/requirement-spec.md', format: 'markdown' as const, content: '# 已编辑' },
    requirementSpec: { title: '已编辑' }
  }
  const h = harness(initial, {
    /** 保存期间模拟另一帧到达，验证写回不会覆盖新字段。 */
    saveRequirementSpecDraft: async (workspaceRoot, spec, threadId) => {
      assert.equal(workspaceRoot, '/workspace/app-A')
      assert.equal(threadId, 'thread-A')
      assert.deepEqual(spec, { title: '用户输入' })
      h.order.push('save_service')
      h.setCurrent({ ...h.current()!, workflow: { ...initial.workflow!, state: { ...initial.workflow!.state, newestField: true } } })
      return saved
    }
  })
  const returned = await h.runtime.saveRequirementSpec({ title: '用户输入' })
  h.order.push('returned')
  assert.deepEqual(h.order, ['save_service', 'workflow_received', 'returned'])
  assert.equal(returned, saved)
  assert.equal(h.current()?.workflow?.state?.newestField, true)
  assert.equal(h.current()?.workflow?.confirmationArtifact, saved.artifact)
  assert.deepEqual(h.current()?.workflow?.result?.requirement_spec, saved.requirementSpec)
}

// H：未挂 Modal 的正式设计修订直接发送原用户请求和 revision 信封。
{
  const h = harness()
  const input = { request: '调整导航', target: { type: 'application' }, impact: { formalBranch: 'design_stage_revision', interactionId: 'impact-1' }, sourceSessionId: 'session', sourceConversationThreadId: 'conversation', sourceRunId: 'source-run' } as WorkflowDesignStageRevisionStart
  await h.runtime.startDesignRevision(input)
  assert.equal(h.calls[0].message, input.request)
  assert.equal(h.calls[0].options.workflowAction, 'start_design_revision')
  assert.equal(h.calls[0].options.workflowDebug, undefined)
  assert.deepEqual(h.calls[0].options.revisionRequest?.confirmedImpact, { interactionId: 'impact-1' })
}

// I：transport settled 不代表业务完成，待确认状态保持原样。
{
  const h = harness()
  h.onSend(async () => result(confirmationWorkflow()))
  await h.runtime.ensureStarted()
  assert.equal(h.events.at(-1)?.type, 'run_settled')
  assert.equal(h.current()?.transportState, 'idle')
  assert.equal(h.current()?.workflow?.summary.status, 'requires_user_input')
  assert.ok(h.order.indexOf('workflow_received') < h.order.indexOf('publish_workflow'))
}

// J：缺中断时先用独立 client 原子恢复，再由唯一 Session 发正式写请求。
{
  const current = planningState()
  current.workflow = workflowWithoutInterrupt()
  const h = harness(current)
  h.onRead(async () => ({
    workflow: confirmationWorkflow('thread-A', 'recovered-gate'),
    lifecycle: authoritativeLifecycle(current, { status: 'awaiting_user' }),
    recovery: recoveryProjection()
  }))
  h.onSend(async (options) => {
    assert.equal(options.applicationPlanningInteraction?.gateId, 'recovered-gate')
    return result(confirmationWorkflow('thread-A', 'submitted-gate'))
  })
  await h.runtime.submitClarification(current.workflow, { __applicationPlanningAction: 'confirm' })
  assert.equal(h.readCalls(), 1)
  assert.equal(h.calls.length, 1)
  assert.equal(h.events.filter((event) => event.type === 'run_started').length, 1)
  assert.equal(h.events.filter((event) => event.type === 'run_settled').length, 1)
}

// K：提交前权威读取期间 Canonical transport 保持 reconciling，写操作被 Runtime 阻止。
{
  const current = planningState()
  current.workflow = workflowWithoutInterrupt()
  const h = harness(current)
  let finishRecovery!: (value: {
    workflow: WorkflowRunPayload
    lifecycle: ApplicationLifecycle
    recovery: ApplicationPlanningRecoveryProjection
  }) => void
  h.onRead(async () => {
    return await new Promise((resolve) => { finishRecovery = resolve })
  })
  const submitting = h.runtime.submitClarification(current.workflow, { __applicationPlanningAction: 'confirm' })
  assert.equal(h.current()?.transportState, 'reconciling')
  await assert.rejects(h.runtime.retryCurrentFailure(), /请先重新同步状态/)
  assert.equal(h.calls.length, 0)
  finishRecovery({
    workflow: confirmationWorkflow(),
    lifecycle: authoritativeLifecycle(current, { status: 'awaiting_user' }),
    recovery: recoveryProjection()
  })
  await submitting
  assert.equal(h.calls.length, 1)
}

// L：并发手动同步共享同一个 single-flight 权威读取。
{
  const h = harness()
  let finishRecovery!: (value: {
    workflow: WorkflowRunPayload
    lifecycle: ApplicationLifecycle
    recovery: ApplicationPlanningRecoveryProjection
  }) => void
  h.onRead(async () => {
    return await new Promise((resolve) => { finishRecovery = resolve })
  })
  const first = h.runtime.reconcileCurrentState()
  const second = h.runtime.reconcileCurrentState()
  assert.equal(h.readCalls(), 1)
  finishRecovery({
    workflow: confirmationWorkflow(),
    lifecycle: authoritativeLifecycle(h.current()!, { status: 'awaiting_user' }),
    recovery: recoveryProjection()
  })
  assert.deepEqual(await first, { status: 'recovered' })
  assert.deepEqual(await second, { status: 'recovered' })
}

// M：恢复 workflow 必须先进入 Canonical State，正式提交只能读取恢复后的门身份。
{
  const current = planningState()
  current.workflow = workflowWithoutInterrupt()
  const h = harness(current)
  h.onRead(async () => ({
    workflow: confirmationWorkflow('thread-A', 'canonical-recovery-gate'),
    lifecycle: authoritativeLifecycle(current, { status: 'awaiting_user' }),
    recovery: recoveryProjection()
  }))
  h.onSend(async (options) => {
    const canonicalInterrupt = h.current()?.workflow?.state?.application_planning_interrupt as Record<string, unknown> | undefined
    assert.equal(canonicalInterrupt?.gateId, 'canonical-recovery-gate')
    assert.equal(options.applicationPlanningInteraction?.gateId, 'canonical-recovery-gate')
    h.order.push('formal_send')
    return result(confirmationWorkflow('thread-A', 'submitted-gate'))
  })
  await h.runtime.submitClarification(current.workflow, { __applicationPlanningAction: 'confirm' })
  assert.ok(h.order.indexOf('reconcile_received') < h.order.indexOf('formal_send'))
}

// N：TechnicalPlan 重试丢失 terminal frame 后自动恢复到技术规划确认，无需重建 Runtime。
{
  const current = planningState()
  current.lifecycle = authoritativeLifecycle(
    current,
    { stage: 'generating_technical_plan', status: 'failed' }
  )
  current.error = '上次技术规划生成失败'
  const h = harness(current)
  h.onSend(async () => { throw new Error('stream closed before RUN_FINISHED') })
  h.onRead(async () => ({
    workflow: confirmationWorkflow(),
    lifecycle: authoritativeLifecycle(
      current,
      { stage: 'awaiting_technical_plan_confirmation', status: 'awaiting_user' },
      current.lifecycle.revision + 1
    ),
    recovery: recoveryProjection()
  }))
  await h.runtime.retryCurrentFailure()
  assert.equal(h.current()?.workflow?.summary.status, 'requires_user_input')
  assert.equal(h.current()?.workflow?.summary.clarification?.mode, 'technical_plan_confirmation')
  assert.equal(h.current()?.transportState, 'idle')
  assert.equal(h.current()?.syncError, undefined)
  assert.equal(h.current()?.error, undefined)
}

// O：提交断线后仍是同一 gate，拒绝提交 promise 且不自动重复 confirm。
{
  const current = planningState()
  current.workflow = confirmationWorkflow('thread-A', 'same-gate')
  const h = harness(current)
  h.onSend(async () => { throw new Error('network disconnected') })
  h.onRead(async () => ({
    workflow: confirmationWorkflow('thread-A', 'same-gate'),
    lifecycle: authoritativeLifecycle(current, { status: 'awaiting_user' }),
    recovery: recoveryProjection()
  }))
  await assert.rejects(
    h.runtime.submitClarification(current.workflow, { __applicationPlanningAction: 'confirm' }),
    /network disconnected/
  )
  assert.equal(h.calls.length, 1)
  assert.equal(h.readCalls(), 1)
  assert.equal(h.current()?.workflow?.state?.application_planning_interrupt &&
    (h.current()?.workflow?.state?.application_planning_interrupt as Record<string, unknown>).gateId, 'same-gate')
}

// P：提交断线后 gate 已变化，视为后端已消费本次动作且不回滚提交。
{
  const current = planningState()
  current.workflow = confirmationWorkflow('thread-A', 'consumed-gate')
  const h = harness(current)
  h.onSend(async () => { throw new Error('unexpected EOF') })
  h.onRead(async () => ({
    workflow: confirmationWorkflow('thread-A', 'next-gate'),
    lifecycle: authoritativeLifecycle(current, { status: 'awaiting_user' }),
    recovery: recoveryProjection()
  }))
  await h.runtime.submitClarification(current.workflow, { __applicationPlanningAction: 'confirm' })
  assert.equal(h.calls.length, 1)
  assert.equal(h.readCalls(), 1)
  assert.equal(
    (h.current()?.workflow?.state?.application_planning_interrupt as Record<string, unknown>).gateId,
    'next-gate'
  )
}

// Q：transport 与 recovery 都失败时只进入 uncertain/syncError，不写业务 error。
{
  const h = harness()
  h.onSend(async () => { throw new Error('fetch failed') })
  h.onRead(async () => { throw new Error('recovery unavailable') })
  await h.runtime.ensureStarted()
  assert.equal(h.current()?.transportState, 'uncertain')
  assert.equal(h.current()?.syncError, 'recovery unavailable')
  assert.equal(h.current()?.error, undefined)
}

// R：手动 reconcile 成功会清理 uncertain/syncError 并原子恢复 idle。
{
  const current = planningState()
  current.transportState = 'uncertain'
  current.syncError = '状态尚未确认'
  const h = harness(current)
  const outcome = await h.runtime.reconcileCurrentState()
  assert.deepEqual(outcome, { status: 'recovered' })
  assert.equal(h.current()?.transportState, 'idle')
  assert.equal(h.current()?.syncError, undefined)
  assert.equal(h.events.filter((event) => event.type === 'reconcile_received').length, 1)
}

// S：冷启动恢复到 awaiting_user 时只展示 checkpoint，不调用 Graph sendMessage。
{
  const current = planningState()
  current.restoreArtifactsFromDisk = true
  current.lifecycle = authoritativeLifecycle(current, { status: 'awaiting_user' })
  const h = harness(current)
  await h.runtime.ensureStarted()
  assert.equal(h.readCalls(), 1)
  assert.equal(h.calls.length, 0)
  assert.equal(h.current()?.workflow?.summary.status, 'requires_user_input')
}

// T：冷启动 lifecycle 仍为 running 但 checkpoint 已是确认门时不得重新生成。
{
  const current = planningState()
  current.restoreArtifactsFromDisk = true
  current.lifecycle = authoritativeLifecycle(
    current,
    { stage: 'generating_technical_plan', status: 'running' }
  )
  const h = harness(current)
  h.onRead(async () => ({
    workflow: confirmationWorkflow(),
    lifecycle: authoritativeLifecycle(
      current,
      { stage: 'awaiting_technical_plan_confirmation', status: 'awaiting_user' }
    ),
    recovery: recoveryProjection()
  }))
  await h.runtime.ensureStarted()
  assert.equal(h.calls.length, 0)
  assert.equal(h.current()?.workflow?.summary.clarification?.mode, 'technical_plan_confirmation')
}

// U：只有 collecting_requirement/pending 且 checkpoint 缺失时允许首次启动 Graph。
{
  const current = planningState()
  current.restoreArtifactsFromDisk = true
  current.lifecycle = authoritativeLifecycle(
    current,
    { stage: 'collecting_requirement', status: 'pending' }
  )
  const h = harness(current)
  h.onRead(async () => {
    throw new ApplicationPlanningCheckpointNotFoundError('checkpoint missing')
  })
  await h.runtime.ensureStarted()
  assert.equal(h.calls.length, 1)
  assert.equal(h.calls[0].options.workflowDebug?.resumeFrom, 'requirements')
}

// V：非初始 running 阶段缺 checkpoint 必须 uncertain，绝不能回退 requirements。
{
  const current = planningState()
  current.restoreArtifactsFromDisk = true
  current.lifecycle = authoritativeLifecycle(
    current,
    { stage: 'generating_technical_plan', status: 'running' }
  )
  const h = harness(current)
  h.onRead(async () => {
    throw new ApplicationPlanningCheckpointNotFoundError('checkpoint missing')
  })
  await h.runtime.ensureStarted()
  assert.equal(h.calls.length, 0)
  assert.equal(h.current()?.transportState, 'uncertain')
  assert.equal(h.current()?.syncError, 'checkpoint missing')
}

// W：stop transport 失败后以权威 stopped lifecycle 收敛，不虚构本地停止结果。
{
  const current = planningState()
  current.workflow = confirmationWorkflow()
  const h = harness(current)
  h.onStop(async () => { throw new Error('cancel timeout') })
  h.onRead(async () => ({
    workflow: confirmationWorkflow(),
    lifecycle: authoritativeLifecycle(current, { status: 'stopped' }),
    recovery: recoveryProjection()
  }))
  await h.runtime.stop()
  assert.equal(h.readCalls(), 1)
  assert.equal(h.current()?.lifecycle.initialization.status, 'stopped')
  assert.equal(h.current()?.transportState, 'idle')
}

// X：reconcile 开始后旧 writer 的晚到 callback 被新 runToken 丢弃。
{
  const h = harness()
  let staleOptions: SendWorkflowMessageOptions | undefined
  let rejectWriter!: (reason: Error) => void
  let finishRecovery!: (value: {
    workflow: WorkflowRunPayload
    lifecycle: ApplicationLifecycle
    recovery: ApplicationPlanningRecoveryProjection
  }) => void
  h.onSend(async (options) => {
    staleOptions = options
    return await new Promise<AgUiChatResult>((_resolve, reject) => { rejectWriter = reject })
  })
  h.onRead(async () => {
    return await new Promise((resolve) => { finishRecovery = resolve })
  })
  const running = h.runtime.ensureStarted()
  rejectWriter(new Error('transport lost'))
  const finishAuthoritativeRecovery = await waitForCondition(
    () => finishRecovery,
    'transport 失败后的权威状态恢复'
  )
  staleOptions?.onWorkflow?.({
    ...confirmationWorkflow(),
    summary: { status: 'failed', message: 'stale callback' }
  })
  finishAuthoritativeRecovery({
    workflow: confirmationWorkflow('thread-A', 'authoritative-gate'),
    lifecycle: authoritativeLifecycle(h.current()!, { status: 'awaiting_user' }),
    recovery: recoveryProjection()
  })
  await running
  assert.equal(h.current()?.workflow?.summary.status, 'requires_user_input')
  assert.equal(h.current()?.error, undefined)
  assert.equal(
    (h.current()?.workflow?.state?.application_planning_interrupt as Record<string, unknown>).gateId,
    'authoritative-gate'
  )
}

// Y：uncertain 状态禁止保存 RequirementSpec，不能触达写接口。
{
  const current = planningState()
  current.transportState = 'uncertain'
  current.syncError = '状态尚未确认'
  current.workflow = confirmationWorkflow()
  let saveCalls = 0
  const h = harness(current, {
    saveRequirementSpecDraft: async () => {
      saveCalls += 1
      throw new Error('uncertain 状态不应调用保存接口')
    }
  })
  await assert.rejects(
    h.runtime.saveRequirementSpec({ title: '不应保存' }),
    /请先重新同步状态/
  )
  assert.equal(saveCalls, 0)
}

// Z：reconciling 状态同样禁止保存 RequirementSpec，不能与权威读取并发写入。
{
  const current = planningState()
  current.transportState = 'reconciling'
  current.workflow = confirmationWorkflow()
  let saveCalls = 0
  const h = harness(current, {
    saveRequirementSpecDraft: async () => {
      saveCalls += 1
      throw new Error('reconciling 状态不应调用保存接口')
    }
  })
  await assert.rejects(
    h.runtime.saveRequirementSpec({ title: '不应保存' }),
    /请先重新同步状态/
  )
  assert.equal(saveCalls, 0)
}

// 补充：服务端明确 RUN_ERROR 保持业务失败，不额外触发 reconcile。
{
  const h = harness()
  h.onSend(async () => {
    throw new AgUiRunError('technical planning failed', {
      workflow: {
        ...confirmationWorkflow(),
        summary: { status: 'failed', message: 'technical planning failed' }
      }
    })
  })
  await h.runtime.ensureStarted()
  assert.equal(h.readCalls(), 0)
  assert.equal(h.current()?.error, 'technical planning failed')
  assert.equal(h.current()?.transportState, 'idle')
}

// 补充：同线程历史卡片不能决定交互门，跨线程卡片明确拒绝。
{
  const current = planningState()
  current.workflow = confirmationWorkflow('thread-A', 'canonical-gate')
  const h = harness(current)
  await h.runtime.submitClarification(confirmationWorkflow('thread-A', 'old-gate'), { __applicationPlanningAction: 'confirm' })
  assert.equal(h.calls[0].options.applicationPlanningInteraction?.gateId, 'canonical-gate')
  await assert.rejects(h.runtime.submitClarification(confirmationWorkflow('thread-B'), { __applicationPlanningAction: 'confirm' }), /确认卡已经失效/)
  h.setCurrent({ ...current, threadId: 'replacement-thread' })
  await assert.rejects(h.runtime.retryCurrentFailure(), /Planning Runtime 已失效/)
}

// 补充：认证错误由全局认证入口处理，不生成运行错误卡。
{
  const h = harness()
  h.onSend(async () => { throw new Error('HTTP 401: expired') })
  await h.runtime.ensureStarted()
  assert.equal(h.events.some((event) => event.type === 'run_failed'), false)
  assert.equal(h.current()?.transportState, 'idle')
}

// AA：Continue 先读取 Backend 分类，再用 transient Recovery session 只提交 sourceRunId。
{
  const current = planningState()
  current.workflow = workflowWithoutInterrupt()
  current.error = '规划执行中断'
  let recoveryOptions: SendWorkflowMessageOptions | undefined
  let recoveryThreadId = ''
  const childWorkflow = {
    ...workflowWithoutInterrupt(),
    runId: 'run-B',
    summary: { status: 'running', phase: 'requirements', message: '继续生成需求' }
  } as WorkflowRunPayload
  const h = harness(current, {
    createRecoverySession: (threadId) => {
      recoveryThreadId = threadId
      return {
        sendMessage: async (_message, options) => {
          recoveryOptions = options
          options.onWorkflow?.(childWorkflow)
          return result(childWorkflow)
        }
      }
    }
  })
  h.onRead(async () => ({
    workflow: {
      ...workflowWithoutInterrupt(),
      summary: { status: 'failed', phase: 'requirements', message: '回答已保存，可以继续。' }
    },
    lifecycle: authoritativeLifecycle(current, { status: 'running' }),
    recovery: recoveryProjection('thread-A', {
      classification: 'ready_to_continue',
      canContinue: true,
      userActionRequired: false,
      inputCommitted: true,
      reasonCode: 'INPUT_COMMITTED_EXECUTION_INTERRUPTED',
      message: '回答已保存，可以继续。'
    })
  }))

  await h.runtime.retryCurrentFailure()

  assert.equal(recoveryThreadId, 'thread-A')
  assert.deepEqual(recoveryOptions?.executionRecovery, {
    action: 'continue',
    sourceRunId: 'run-A'
  })
  assert.equal(recoveryOptions?.applicationPlanningInteraction, undefined)
  assert.equal(recoveryOptions?.workflowDebug, undefined)
  assert.equal(h.calls.length, 0)
  assert.equal(h.current()?.workflow?.runId, 'run-B')
}

// AB：回答已被 checkpoint 消费时 transport 失败不回滚、不重发原答案。
{
  const current = planningState()
  current.workflow = confirmationWorkflow('thread-A', 'submitted-gate')
  const h = harness(current)
  h.onSend(async () => { throw new Error('connection lost') })
  h.onRead(async () => ({
    workflow: {
      ...workflowWithoutInterrupt(),
      summary: { status: 'failed', phase: 'requirements', message: '回答已保存，可以继续。' }
    },
    lifecycle: authoritativeLifecycle(current, { status: 'running' }),
    recovery: recoveryProjection('thread-A', {
      classification: 'ready_to_continue',
      canContinue: true,
      userActionRequired: false,
      inputCommitted: true,
      reasonCode: 'INPUT_COMMITTED_EXECUTION_INTERRUPTED',
      message: '回答已保存，可以继续。'
    })
  }))

  await h.runtime.submitClarification(current.workflow, {
    __applicationPlanningAction: 'answer',
    role: '本人'
  })

  assert.equal(h.calls.length, 1)
  assert.equal(h.readCalls(), 1)
  assert.equal(h.current()?.recovery?.inputCommitted, true)
}

console.log('application planning runtime tests passed')
