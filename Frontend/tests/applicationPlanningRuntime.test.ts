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
import type { AgUiChatResult, SendWorkflowMessageOptions } from '../src/renderer/src/service/agUiAgent'
import type { ApplicationConfig, WorkflowDesignStageRevisionStart, WorkflowRunPayload } from '../src/renderer/src/typings'

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

/** 注入可控会话与真实 Canonical reducer，记录所有外部调用和到达顺序。 */
function harness(initial = planningState(), overrides: Partial<ApplicationPlanningRuntimeDependencies> = {}) {
  let current: ApplicationPlanningCurrentState | undefined = initial
  let active = false
  let stopCalls = 0
  let send: (options: SendWorkflowMessageOptions) => Promise<AgUiChatResult> = async () => result()
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
    session: {
      /** 记录标准 AG-UI options 并模拟传输是否活动。 */
      sendMessage: async (message, options) => {
        calls.push({ message, options })
        active = true
        try { return await send(options) } finally { active = false }
      },
      /** 停止只影响本测试实例的会话。 */
      stop: async () => { stopCalls += 1; active = false },
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
    /** 查询本实例 stop 调用次数。 */
    stopCalls: () => stopCalls
  }
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

// B：awaiting_user 自动发只读恢复，恢复状态描述不进入聊天正文。
{
  const current = planningState()
  current.lifecycle.initialization.status = 'awaiting_user'
  const h = harness(current)
  h.onSend(async (options) => {
    options.onContent?.('已恢复待确认规划')
    return result(confirmationWorkflow())
  })
  await h.runtime.ensureStarted()
  assert.equal(h.calls[0].options.applicationPlanningRecovery?.action, 'get')
  assert.equal(h.calls[0].options.applicationPlanningRecovery?.applicationId, 'app-A')
  assert.equal(h.calls[0].options.workflowDebug, undefined)
  assert.deepEqual(h.contents, [])
}

// C：确认帧即使随后遇到 transport 错误，也先进入 Canonical State。
{
  const h = harness()
  h.onSend(async (options) => {
    options.onWorkflow?.(confirmationWorkflow())
    assert.equal(h.current()?.workflow?.summary.status, 'requires_user_input')
    throw new Error('transport interrupted')
  })
  await h.runtime.ensureStarted()
  assert.ok(h.order.indexOf('workflow_received') < h.order.indexOf('run_failed'))
  assert.equal(h.current()?.workflow?.summary.clarification?.mode, 'technical_plan_confirmation')
  assert.equal(h.current()?.error, 'transport interrupted')
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

// J：缺中断的恢复读取和正式提交共享一个 logical transport ownership。
{
  const current = planningState()
  current.workflow = workflowWithoutInterrupt()
  const h = harness(current)
  h.onSend(async (options) => {
    if (h.calls.length === 1) {
      assert.equal(options.applicationPlanningRecovery?.action, 'get')
      return result(confirmationWorkflow('thread-A', 'recovered-gate'))
    }
    assert.equal(options.applicationPlanningInteraction?.gateId, 'recovered-gate')
    return result(confirmationWorkflow('thread-A', 'submitted-gate'))
  })
  await h.runtime.submitClarification(current.workflow, { __applicationPlanningAction: 'confirm' })
  assert.equal(h.calls.length, 2)
  assert.equal(h.events.filter((event) => event.type === 'run_started').length, 1)
  assert.equal(h.events.filter((event) => event.type === 'run_settled').length, 1)
}

// K：提交前恢复等待期间 Canonical transport 保持 running，普通重试不能并发发送。
{
  const current = planningState()
  current.workflow = workflowWithoutInterrupt()
  const h = harness(current)
  let finishRecovery!: (value: AgUiChatResult) => void
  h.onSend(async () => {
    if (h.calls.length === 1) {
      return await new Promise<AgUiChatResult>((resolve) => { finishRecovery = resolve })
    }
    return result(confirmationWorkflow())
  })
  const submitting = h.runtime.submitClarification(current.workflow, { __applicationPlanningAction: 'confirm' })
  assert.equal(h.current()?.transportState, 'running')
  await h.runtime.retryCurrentFailure()
  assert.equal(h.calls.length, 1)
  finishRecovery(result(confirmationWorkflow()))
  await submitting
  assert.equal(h.calls.length, 2)
}

// L：恢复重试属于同一 ownership，前两次读取和正式提交只产生一对运行事件。
{
  const current = planningState()
  current.workflow = workflowWithoutInterrupt()
  const h = harness(current)
  h.onSend(async () => {
    if (h.calls.length === 1) throw new Error('temporary network error')
    if (h.calls.length === 2) return result(confirmationWorkflow('thread-A', 'retry-gate'))
    return result(confirmationWorkflow('thread-A', 'submitted-gate'))
  })
  await h.runtime.submitClarification(current.workflow, { __applicationPlanningAction: 'confirm' })
  assert.equal(h.calls.length, 3)
  assert.equal(h.events.filter((event) => event.type === 'run_started').length, 1)
  assert.equal(h.events.filter((event) => event.type === 'run_settled').length, 1)
}

// M：恢复 workflow 必须先进入 Canonical State，正式提交只能读取恢复后的门身份。
{
  const current = planningState()
  current.workflow = workflowWithoutInterrupt()
  const h = harness(current)
  h.onSend(async (options) => {
    if (h.calls.length === 1) return result(confirmationWorkflow('thread-A', 'canonical-recovery-gate'))
    const canonicalInterrupt = h.current()?.workflow?.state?.application_planning_interrupt as Record<string, unknown> | undefined
    assert.equal(canonicalInterrupt?.gateId, 'canonical-recovery-gate')
    assert.equal(options.applicationPlanningInteraction?.gateId, 'canonical-recovery-gate')
    h.order.push('formal_send')
    return result(confirmationWorkflow('thread-A', 'submitted-gate'))
  })
  await h.runtime.submitClarification(current.workflow, { __applicationPlanningAction: 'confirm' })
  assert.ok(h.order.indexOf('workflow_received') < h.order.indexOf('formal_send'))
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

console.log('application planning runtime tests passed')
