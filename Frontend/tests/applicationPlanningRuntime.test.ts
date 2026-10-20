import assert from 'node:assert/strict'
import './formalRevisionHandoffRecovery.test'
import './formalRevisionStartRetry.test'
import './phaseSessionSelection.test'
import './planningStageReentry.test'
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
  uiDesignRecoveryError,
  type ApplicationPlanningRecoveryProjection
} from '../src/renderer/src/service/applicationPlanningRecovery'
import type {
  ApplicationConfig,
  ApplicationLifecycle,
  WorkflowDesignStageRevisionStart,
  WorkflowRunPayload
} from '../src/renderer/src/typings'
import { initialConnectionState } from '../src/renderer/src/service/connectionState'
import { applicationPlanningRecoveryIncident } from '../src/renderer/src/service/recoveryIncident'

/** 构造带稳定身份的最小 Planning 当前状态，不挂载任何 React 视图。 */
function planningState(applicationId = 'app-A', threadId = 'thread-A'): ApplicationPlanningCurrentState {
  const application = { id: applicationId, appName: applicationId, workspaceRoot: `/workspace/${applicationId}` } as ApplicationConfig
  return {
    application, threadId, transportState: 'idle', connection: initialConnectionState(true),
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
  const classification = overrides.classification || 'awaiting_user'
  const sourceRunId = overrides.sourceRunId || 'run-A'
  const recoveryActionPlan =
    classification === 'ready_to_continue'
      ? {
          schemaVersion: 'recovery-action-plan.v1' as const,
          incidentId: 'incident-A',
          sourceRunId,
          threadId,
          executionKind: 'application_planning' as const,
          status: 'recoverable' as const,
          reasonCode: 'RECOVERABLE_TEST',
          message: '当前执行现场可以安全继续。',
          primaryAction: {
            actionId: 'action-A',
            kind: 'continue_checkpoint' as const,
            label: '继续执行',
            description: '从测试 checkpoint 继续。',
            requiresConfirmation: false
          },
          alternateActions: []
        }
      : null
  return {
    schemaVersion: 'application-planning-recovery.v1',
    classification,
    sourceRunId,
    threadId,
    canContinue: false,
    userActionRequired: true,
    inputCommitted: false,
    reasonCode: 'NATIVE_APPLICATION_PLANNING_INTERRUPT',
    message: '当前应用规划正在等待你的确认。',
    recoveryActionPlan,
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
  let read: () => Promise<import('../src/renderer/src/service/applicationPlanningRecovery').ApplicationPlanningAuthoritativeSnapshot | undefined> = async () => ({
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
  assert.equal(h.current()?.connection.status, 'healthy')
  assert.equal(h.current()?.transportState, 'idle')
}

// D：生命周期失败但没有 Backend 签发的恢复动作时，不能猜测节点并启动重试。
{
  const h = harness()
  const current = h.current()!
  h.setCurrent({ ...current, lifecycle: { ...current.lifecycle, initialization: { ...current.lifecycle.initialization, stage: 'generating_technical_plan', status: 'failed' } } })
  await h.runtime.retryCurrentFailure()
  assert.equal(h.calls.length, 0)
}

// E：两个应用各自持有会话、事件和流式订阅。
{
  const h = harness()
  const current = h.current()!
  h.setCurrent({
    ...current,
    lifecycle: {
      ...current.lifecycle,
      activeFormalRevision: {
        changeId: 'change-template', formalBranch: 'design_stage_revision',
        impactInteractionId: 'impact-template', sourceThreadId: 'thread-source',
        sourceRunId: 'run-source', planningThreadId: 'thread-A',
        status: 'template_reconcile_failed'
      }
    }
  })
  h.onRead(async () => ({ lifecycle: h.current()!.lifecycle, workflow: confirmationWorkflow(), recovery: recoveryProjection() }))
  await h.runtime.retryTemplateReconcile()
  assert.equal(h.calls[0].options.workflowAction, 'retry_template_reconcile')
  assert.equal(h.calls[0].options.workflowDebug, undefined)
}

// Template Reconcile 断线后仍可先校准，但校准失败或失去重试资格时不能发起 Graph 动作。
{
  const h = harness()
  const current = h.current()!
  const lifecycle = { ...current.lifecycle, activeFormalRevision: {
    changeId: 'change-template', formalBranch: 'design_stage_revision' as const,
    impactInteractionId: 'impact-template', sourceThreadId: 'thread-source',
    sourceRunId: 'run-source', planningThreadId: 'thread-A', status: 'template_reconcile_failed' as const
  } }
  h.setCurrent({ ...current, lifecycle, connection: { ...current.connection, status: 'unavailable' } })
  h.onRead(async () => { throw new TypeError('Backend 不可达') })
  await h.runtime.retryTemplateReconcile()
  assert.equal(h.calls.length, 0)
  assert.equal(h.current()?.connection.status, 'unavailable')
  h.onRead(async () => ({ lifecycle, workflow: confirmationWorkflow(), recovery: recoveryProjection() }))
  await h.runtime.retryTemplateReconcile()
  assert.equal(h.calls.length, 1)
  assert.equal(h.calls[0].options.workflowAction, 'retry_template_reconcile')
  assert.equal(h.calls[0].options.workflowDebug, undefined)
}
{
  const h = harness()
  await h.runtime.retryTemplateReconcile()
  assert.equal(h.calls.length, 0)
}

// F：两个应用各自持有会话、事件和流式订阅。
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
// 技术专项修订先替换 Canonical 旧规划，再发布新待确认门；不能只写到聊天消息。
{
  const current = planningState()
  current.workflow = { ...confirmationWorkflow(), runId: 'old-confirmed-run',
    summary: { status: 'completed', phase: 'technical_planning' },
    state: { technical_plan: { confirmation_status: 'confirmed', architecture: { old: true } } } }
  const h = harness(current)
  const pending = { ...confirmationWorkflow('thread-A', 'new-revision-gate'), runId: 'new-plan-run',
    state: { application_planning_interrupt: { gateId: 'new-revision-gate' },
      technical_plan: { confirmation_status: 'pending_user_confirmation', architecture: { fresh: true } } } }
  h.onSend(async (options) => {
    assert.equal(options.workflowAction, 'start_technical_revision')
    assert.equal(options.workflowScope, 'application_planning')
    options.onWorkflow?.(pending)
    assert.equal(h.current()?.workflow?.runId, 'new-plan-run')
    return result(pending)
  })
  await h.runtime.startDesignRevision({ request: '提取只读组件', target: { type: 'page', pageId: 'journey' },
    impact: { formalBranch: 'workbench_plan_revision', interactionId: 'plan-impact' },
    sourceSessionId: 'source', sourceConversationThreadId: 'conversation', sourceRunId: 'source-run' } as WorkflowDesignStageRevisionStart)
  assert.equal(h.current()?.workflow?.summary.status, 'requires_user_input')
  assert.equal((h.current()?.workflow?.state?.technical_plan as Record<string, unknown>)?.confirmation_status, 'pending_user_confirmation')
  assert.equal(h.published.at(-1)?.runId, 'new-plan-run')
}

// 设计入场拒绝必须回传调用方，不能被随后读取的旧成功 checkpoint 吞掉。
{
  const h = harness()
  const input = { request: '调整导航', target: { type: 'page', pageId: 'page' }, impact: { formalBranch: 'design_stage_revision', interactionId: 'impact-1' }, sourceSessionId: 'session', sourceConversationThreadId: 'conversation', sourceRunId: 'source-run' } as WorkflowDesignStageRevisionStart
  h.onSend(async () => result({ runId: 'rejected-start', threadId: 'thread-A', events: [],
    summary: { status: 'failed', phase: 'design_intent_analysis', message: '目标不匹配' }, state: {} }))
  await assert.rejects(h.runtime.startDesignRevision(input), /目标不匹配/)
  assert.equal(h.current()?.transportState, 'idle')
  assert.ok(h.events.some((event) => event.type === 'run_failed'))
}

// 恢复执行期间旧中断卡退让；失败收口后原证据仍可展示，不提前删除恢复事实。
{
  const current = planningState()
  current.recovery = recoveryProjection('thread-A', { classification: 'ready_to_continue' })
  assert.equal(applicationPlanningRecoveryIncident(current)?.kind, 'recoverable')
  const running = reduceApplicationPlanningCurrentState(current, {
    type: 'run_started', applicationId: 'app-A', threadId: 'thread-A'
  })
  assert.equal(applicationPlanningRecoveryIncident(running), undefined)
  assert.equal(running.recovery, current.recovery)
  const settled = reduceApplicationPlanningCurrentState(running, {
    type: 'run_settled', applicationId: 'app-A', threadId: 'thread-A'
  })
  assert.equal(applicationPlanningRecoveryIncident(settled)?.kind, 'recoverable')
}

// 正常停止展示中性继续入口，真实失败诊断不能被「继续生成」文案隐藏。
{
  const current = planningState()
  const recovery = recoveryProjection('thread-A', { classification: 'ready_to_continue' })
  recovery.recoveryActionPlan!.primaryAction!.label = '继续生成'
  current.recovery = recovery
  const stopped = applicationPlanningRecoveryIncident(current)
  assert.equal(stopped?.title, '已停止生成')
  assert.equal(stopped?.kind === 'recoverable' && stopped.presentation, 'stopped_generation')
  assert.equal(stopped?.failureMessage, undefined)
  recovery.failureDiagnostic = { sourceRunId: 'run-A', origin: 'model', code: 'MODEL_FAILED', message: '模型调用失败' }
  const failed = applicationPlanningRecoveryIncident(current)
  assert.notEqual(failed?.kind === 'recoverable' && failed.presentation, 'stopped_generation')
  assert.equal(failed?.failureDiagnostic?.message, '模型调用失败')
}

// 正式修订交接后停服，即使只读对账也断线，启动调用不能触发删除设计会话的回滚。
{
  const h = harness()
  const input = { request: '增加异常流程测试页', target: { type: 'application' }, impact: { formalBranch: 'design_stage_revision', interactionId: 'impact-entered' }, sourceSessionId: 'source-session', sourceConversationThreadId: 'source-thread', sourceRunId: 'source-run' } as WorkflowDesignStageRevisionStart
  h.onSend(async () => {
    const current = h.current()!
    h.setCurrent({ ...current, lifecycle: {
      ...current.lifecycle,
      activeFormalRevision: {
        changeId: 'change-entered', formalBranch: 'design_stage_revision',
        impactInteractionId: input.impact.interactionId,
        sourceThreadId: input.sourceConversationThreadId, sourceRunId: input.sourceRunId,
        planningThreadId: 'thread-A', status: 'design_planning', currentArtifact: 'requirement-spec'
      }
    } })
    throw new Error('Failed to fetch')
  })
  h.onRead(async () => { throw new Error('Backend unavailable') })
  await h.runtime.startDesignRevision(input)
  assert.equal(h.current()?.lifecycle.activeFormalRevision?.currentArtifact, 'requirement-spec')
  assert.equal(h.current()?.connection.status, 'unavailable')
  assert.equal(h.calls.length, 1)
  assert.equal(h.current()?.transportState, 'idle')
}

// 尚未收到回执的网络中断也不能被当成未入场证明；明确业务拒绝仍向上层抛错。
{
  const input = { request: '增加页面', target: { type: 'application' }, impact: { formalBranch: 'design_stage_revision', interactionId: 'impact-unacknowledged' }, sourceSessionId: 'source-session', sourceConversationThreadId: 'source-thread', sourceRunId: 'source-run' } as WorkflowDesignStageRevisionStart
  const uncertain = harness()
  uncertain.onSend(async () => { throw new Error('Failed to fetch') })
  uncertain.onRead(async () => { throw new Error('Backend unavailable') })
  await uncertain.runtime.startDesignRevision(input)
  assert.equal(uncertain.calls.length, 1)
  const rejected = harness()
  rejected.onSend(async () => { throw new AgUiRunError('impact 已过期') })
  await assert.rejects(rejected.runtime.startDesignRevision(input), /impact 已过期/)
}

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

// K：权威读取期间允许显式重试共享读取，不因 reconciling 拒绝用户操作。
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
  const retrying = h.runtime.retryCurrentFailure()
  assert.equal(h.current()?.recoveryActionError, undefined)
  assert.equal(h.calls.length, 0)
  finishRecovery({
    workflow: confirmationWorkflow(),
    lifecycle: authoritativeLifecycle(current, { status: 'awaiting_user' }),
    recovery: recoveryProjection()
  })
  await Promise.all([submitting, retrying])
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
  assert.equal(h.current()?.connection.status, 'healthy')
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

// Q：transport 与 durable refresh 都失败时只更新 Connection State，不写业务 error。
{
  const h = harness()
  h.onSend(async () => { throw new Error('fetch failed') })
  h.onRead(async () => { throw new Error('recovery unavailable') })
  await h.runtime.ensureStarted()
  assert.equal(h.current()?.transportState, 'idle')
  assert.equal(h.current()?.connection.status, 'unavailable')
  assert.equal(h.current()?.connection.lastError, 'recovery unavailable')
  assert.equal(h.current()?.error, undefined)
}

// R：手动 reconcile 成功会恢复 Connection healthy 并原子恢复 idle。
{
  const current = planningState()
  current.connection = {
    ...current.connection,
    status: 'unavailable',
    lastError: '状态尚未确认'
  }
  const h = harness(current)
  const outcome = await h.runtime.reconcileCurrentState()
  assert.deepEqual(outcome, { status: 'recovered' })
  assert.equal(h.current()?.transportState, 'idle')
  assert.equal(h.current()?.connection.status, 'healthy')
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

// T2：冷启动读到可恢复 ActionPlan 时必须停下等用户点击，不能按旧 running lifecycle 自动重发。
{
  const initial = planningState()
  initial.restoreArtifactsFromDisk = true
  const h = harness(initial)
  h.onRead(async () => ({
    workflow: workflowWithoutInterrupt(),
    lifecycle: authoritativeLifecycle(initial, { status: 'running' }),
    recovery: recoveryProjection('thread-A', { classification: 'ready_to_continue' })
  }))

  await h.runtime.ensureStarted()

  assert.equal(h.calls.length, 0)
  assert.equal(h.current()?.recovery?.recoveryActionPlan?.status, 'recoverable')
  assert.equal(h.current()?.transportState, 'idle')
}

// T3：冷启动对账仍在运行且尚无恢复动作时，也不得创建新的普通阶段 Run。
{
  const initial = planningState()
  initial.restoreArtifactsFromDisk = true
  initial.lifecycle = authoritativeLifecycle(initial, {
    stage: 'generating_technical_plan', status: 'running'
  })
  const h = harness(initial)
  const running = {
    ...confirmationWorkflow(),
    summary: { status: 'running', phase: 'technical_planning_generate' },
    state: {},
    result: {}
  } as WorkflowRunPayload
  h.onRead(async () => ({
    workflow: running,
    lifecycle: authoritativeLifecycle(initial),
    recovery: recoveryProjection('thread-A', {
      classification: 'running', userActionRequired: false, recoveryActionPlan: null
    })
  }))

  await h.runtime.ensureStarted()
  await h.runtime.ensureStarted()
  assert.equal(h.readCalls(), 1)
  assert.equal(h.calls.length, 0)
  assert.equal(h.current()?.workflow?.summary.status, 'running')

  // 原执行稍后被后端识别为中断，只读同步应展示兜底动作，仍不能自动执行。
  h.onRead(async () => ({
    workflow: running,
    lifecycle: authoritativeLifecycle(h.current()!),
    recovery: recoveryProjection('thread-A', { classification: 'ready_to_continue' })
  }))
  await h.runtime.refreshDetachedRunningState()
  assert.equal(h.readCalls(), 2)
  assert.equal(h.calls.length, 0)
  assert.equal(h.current()?.recovery?.recoveryActionPlan?.primaryAction?.kind, 'continue_checkpoint')
  await h.runtime.refreshDetachedRunningState()
  assert.equal(h.readCalls(), 2)
}

// T4：本地执行中、状态不确定或 Runtime 已释放时，不让后台只读校准干扰当前动作。
{
  const current = planningState()
  current.recovery = recoveryProjection('thread-A', { classification: 'running', recoveryActionPlan: null })
  current.transportState = 'running'
  const h = harness(current)
  await h.runtime.refreshDetachedRunningState()
  assert.equal(h.readCalls(), 0)
  h.setCurrent({ ...current, transportState: 'reconciling' })
  await h.runtime.refreshDetachedRunningState()
  assert.equal(h.readCalls(), 0)
  h.setCurrent({ ...current, transportState: 'idle', connection: { ...current.connection, status: 'unavailable' } })
  await h.runtime.refreshDetachedRunningState()
  assert.equal(h.readCalls(), 0)
  h.runtime.dispose()
  await h.runtime.refreshDetachedRunningState()
  assert.equal(h.readCalls(), 0)
}

// UI 生成池等待确认时也要检测 Backend；IPC 仍能读文件不能冒充服务健康。
{
  const initial = planningState()
  initial.workflow = { runId: 'ui-pool', threadId: initial.threadId, events: [],
    summary: { status: 'requires_user_input', phase: 'ui_confirmation' } }
  initial.recovery = recoveryProjection(initial.threadId, { classification: 'awaiting_user', recoveryActionPlan: null })
  const h = harness(initial)
  h.onRead(async () => {
    assert.equal(h.current()?.connection.status, 'healthy')
    assert.equal(h.current()?.transportState, 'idle')
    throw new TypeError('Failed to fetch')
  })
  await h.runtime.refreshDetachedRunningState()
  assert.equal(h.readCalls(), 1)
  assert.equal(h.calls.length, 0)
  assert.equal(h.current()?.connection.status, 'unavailable')
  assert.equal(h.current()?.workflow, initial.workflow)
  assert.equal(h.current()?.recovery, initial.recovery)
  assert.equal(h.events.some((event) => event.type === 'reconcile_started'), false)
  await h.runtime.refreshDetachedRunningState()
  assert.equal(h.readCalls(), 1)
  // 重启后由用户显式同步，读取孤立生成页的恢复入口，不自动重新生成或确认。
  h.onRead(async () => ({ workflow: initial.workflow!, lifecycle: authoritativeLifecycle(initial),
    recovery: recoveryProjection(initial.threadId, { classification: 'awaiting_user',
      uiGenerationRecovery: { pageIds: ['interrupted-page'] }, recoveryActionPlan: null }) }))
  await h.runtime.reconcileCurrentState()
  assert.equal(h.current()?.connection.status, 'healthy')
  assert.deepEqual(h.current()?.recovery?.uiGenerationRecovery?.pageIds, ['interrupted-page'])
  assert.equal(h.calls.length, 0)
}

// 重试前后正常同步只改变 transport，不把仍可达的 Backend 宣称为正在重连。
{
  const initial = planningState()
  initial.workflow = confirmationWorkflow()
  const h = harness(initial)
  let resolveRead!: (value: Awaited<ReturnType<ApplicationPlanningRuntimeDependencies['readAuthoritativeSnapshot'] & {}>>) => void
  h.onRead(async () => new Promise((resolve) => { resolveRead = resolve }))
  const pending = h.runtime.reconcileCurrentState()
  assert.equal(h.current()?.transportState, 'reconciling')
  assert.equal(h.current()?.connection.status, 'healthy')
  resolveRead({ workflow: initial.workflow, lifecycle: authoritativeLifecycle(initial), recovery: recoveryProjection() })
  await pending
  assert.equal(h.current()?.connection.status, 'healthy')
  assert.equal(h.calls.length, 0)
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
    return undefined
  })
  await h.runtime.ensureStarted()
  assert.equal(h.calls.length, 1)
  assert.equal(h.calls[0].options.workflowDebug?.resumeFrom, 'requirements')
}

// V：已有应用无 checkpoint 是正常无恢复结果，不报错，也不重跑 requirements。
{
  const current = planningState()
  current.restoreArtifactsFromDisk = true
  current.lifecycle = authoritativeLifecycle(
    current,
    { stage: 'generating_technical_plan', status: 'running' }
  )
  const h = harness(current)
  h.onRead(async () => {
    return undefined
  })
  await h.runtime.ensureStarted()
  assert.equal(h.calls.length, 0)
  assert.equal(h.current()?.transportState, 'idle')
  assert.equal(h.current()?.connection.status, 'healthy')
  assert.equal(h.current()?.connection.lastError, undefined)
  assert.equal(h.current()?.checkpointMissing, true)
  assert.equal(h.current()?.workflow, undefined)
  assert.equal(h.current()?.recovery, undefined)
}

// 无存档会移除旧确认、错误和恢复入口；反复点重试也不派发 Graph。
{
  const initial = planningState()
  initial.restoreArtifactsFromDisk = true
  initial.workflow = confirmationWorkflow()
  initial.error = '上次执行失败'
  initial.recoveryActionError = '上次重试失败'
  initial.recovery = recoveryProjection('thread-A')
  const h = harness(initial)
  h.onRead(async () => undefined)
  await h.runtime.ensureStarted()
  await h.runtime.retryCurrentFailure()
  assert.equal(h.calls.length, 0)
  assert.equal(h.current()?.connection.status, 'healthy')
  assert.equal(h.current()?.checkpointMissing, true)
  assert.equal(h.current()?.workflow, undefined)
  assert.equal(h.current()?.recovery, undefined)
  assert.equal(h.current()?.error, undefined)
  assert.equal(h.current()?.recoveryActionError, undefined)
}

// 正式返回设计后结束必须完成后端 End，再收口旧连接并清除本地恢复残留。
{
  const current = planningState()
  current.lifecycle.activeFormalRevision = { changeId: 'design-change', planningThreadId: current.threadId } as any
  current.workflow = confirmationWorkflow(current.threadId)
  current.error = '旧错误'
  current.recovery = recoveryProjection(current.threadId, { classification: 'ready_to_continue' })
  const completed = authoritativeLifecycle(current, { stage: 'ready_for_workbench', status: 'completed' })
  completed.activeFormalRevision = undefined
  completed.revision = current.lifecycle.revision + 1
  const order: string[] = []
  const h = harness(current, {
    /** 只有后端真正完成修订收口，才允许发布结束成功。 */
    endPlanningExecution: async () => { order.push('backend-end'); return completed }
  })
  h.onStop(async () => { order.push('transport-stop'); throw new Error('旧连接已退出') })
  await h.runtime.end()
  assert.deepEqual(order, ['backend-end', 'transport-stop'])
  assert.equal(h.current()?.lifecycle.activeFormalRevision, undefined)
  assert.equal(h.current()?.recovery, undefined)
  assert.equal(h.current()?.error, undefined)
  assert.equal(h.current()?.transportState, 'idle')
  assert.equal(h.current()?.workflow?.summary.planControl?.status, 'ended')
  assert.equal(h.current()?.workflow?.state?.workflow_scope, 'application_planning')
  assert.match(h.contents.at(-1) || '', /任务已结束/)
}

// 生成期间主动 End 导致旧交接请求先断开时，等 End 成功后正常返回来源会话。
{
  const current = planningState()
  current.lifecycle.activeFormalRevision = { changeId: 'design-change', planningThreadId: current.threadId } as any
  let rejectGeneration!: (reason: Error) => void
  const completed = authoritativeLifecycle(current, { stage: 'ready_for_workbench', status: 'completed' })
  completed.activeFormalRevision = undefined
  const h = harness(current, {
    /** 模拟取消先断开旧请求，服务端随后完成结束并确认清锁。 */
    endPlanningExecution: async () => {
      rejectGeneration(new Error('network error'))
      await new Promise((resolve) => setTimeout(resolve, 0))
      return completed
    }
  })
  h.onSend(async () => new Promise<AgUiChatResult>((_, reject) => { rejectGeneration = reject }))
  const input = { request: '新增说明页面', target: { type: 'application' }, impact: { formalBranch: 'design_stage_revision', interactionId: 'impact-end' }, sourceSessionId: 'session', sourceConversationThreadId: 'conversation', sourceRunId: 'source-run' } as WorkflowDesignStageRevisionStart
  const starting = h.runtime.startDesignRevision(input)
  await waitForCondition(() => rejectGeneration, '交接请求已进入生成')
  await h.runtime.end()
  await starting
  assert.equal(h.current()?.workflow?.summary.planControl?.status, 'ended')
  assert.equal(h.current()?.lifecycle.activeFormalRevision, undefined)
  assert.equal(h.current()?.error, undefined)
}

// End 未确认退出时保留修订；再次结束仍调用 End，不能误入 checkpoint 重试。
{
  const current = planningState()
  current.lifecycle.activeFormalRevision = { changeId: 'design-change', planningThreadId: current.threadId } as any
  let attempts = 0
  const h = harness(current, {
    /** 模拟第一次退出尚未完成，第二次正常结束。 */
    endPlanningExecution: async () => {
      if (++attempts === 1) throw new Error('后台仍在退出')
      const lifecycle = authoritativeLifecycle(current, { stage: 'ready_for_workbench', status: 'completed' })
      lifecycle.activeFormalRevision = undefined
      lifecycle.revision += 1
      return lifecycle
    }
  })
  await assert.rejects(h.runtime.end(), /后台仍在退出/)
  assert.ok(h.current()?.lifecycle.activeFormalRevision)
  assert.equal(h.current()?.endRequested, true)
  await h.runtime.end()
  assert.equal(attempts, 2)
  assert.equal(h.current()?.lifecycle.activeFormalRevision, undefined)
  assert.equal(h.current()?.recoveryActionError, undefined)
  assert.equal(h.current()?.endRequested, false)
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
  await h.runtime.end()
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

// Y：上次连接失败不能阻止用户再次提交保存，实际错误由本次服务返回。
{
  const current = planningState()
  current.connection = {
    ...current.connection,
    status: 'unavailable',
    lastError: '状态尚未确认'
  }
  current.workflow = confirmationWorkflow()
  let saveCalls = 0
  const h = harness(current, {
    saveRequirementSpecDraft: async () => {
      saveCalls += 1
      throw new Error('本次保存服务失败')
    }
  })
  await assert.rejects(
    h.runtime.saveRequirementSpec({ title: '重新保存' }),
    /本次保存服务失败/
  )
  assert.equal(saveCalls, 1)
}

// Z：共享 reconciling 状态不能在本地拒绝用户保存。
{
  const current = planningState()
  current.transportState = 'reconciling'
  current.workflow = confirmationWorkflow()
  let saveCalls = 0
  const h = harness(current, {
    saveRequirementSpecDraft: async () => {
      saveCalls += 1
      throw new Error('本次保存服务失败')
    }
  })
  await assert.rejects(
    h.runtime.saveRequirementSpec({ title: '重新保存' }),
    /本次保存服务失败/
  )
  assert.equal(saveCalls, 1)
}

// 补充：服务端明确 RUN_ERROR 收口后必须重新读取当前 recovery head，并保留真实失败原因。
{
  const h = harness()
  h.onRead(async () => ({
    workflow: {
      ...workflowWithoutInterrupt(),
      summary: { status: 'failed', message: '上一次模型调用失败，可以继续。' }
    },
    lifecycle: authoritativeLifecycle(h.current()!, { status: 'failed' }),
    recovery: recoveryProjection('thread-A', {
      classification: 'ready_to_continue',
      canContinue: true,
      userActionRequired: false,
      reasonCode: 'APPLICATION_PLANNING_MODEL_GENERATION_REPLAY_SAFE',
      message: '当前执行现场可以安全继续。',
      failureDiagnostic: {
        sourceRunId: 'run-A',
        origin: 'model_call',
        code: 'http_503',
        httpStatus: 503,
        model: 'mimo-v2.5-pro',
        message: 'Service Unavailable'
      }
    })
  }))
  h.onSend(async () => {
    throw new AgUiRunError('503 Service Unavailable', {
      workflow: {
        ...confirmationWorkflow(),
        summary: { status: 'failed', message: '503 Service Unavailable' }
      }
    })
  })
  await h.runtime.ensureStarted()
  assert.equal(h.readCalls(), 1)
  assert.equal(h.current()?.recovery?.classification, 'ready_to_continue')
  assert.equal(h.current()?.error, 'Service Unavailable')
  assert.equal(h.current()?.recovery?.message, '当前执行现场可以安全继续。')
  assert.equal(h.current()?.transportState, 'idle')
}

// 补充：旧记录没有 diagnostic 时，ready_to_continue 仍按 Workflow/lifecycle/current error 顺序恢复错误。
{
  const initial = planningState()
  initial.error = '503 Service Unavailable'
  const h = harness(initial)
  h.onRead(async () => ({
    workflow: {
      ...workflowWithoutInterrupt(),
      summary: { status: 'failed', message: '503 Service Unavailable' }
    },
    lifecycle: authoritativeLifecycle(initial, { status: 'failed' }),
    recovery: recoveryProjection('thread-A', {
      classification: 'ready_to_continue',
      canContinue: true,
      userActionRequired: false,
      reasonCode: 'APPLICATION_PLANNING_MODEL_GENERATION_REPLAY_SAFE',
      message: '当前执行现场可以安全继续。'
    })
  }))
  h.onSend(async () => {
    throw new AgUiRunError('transport failed')
  })
  await h.runtime.ensureStarted()
  assert.equal(h.current()?.error, '503 Service Unavailable')
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

// AA：断连时一次 Retry 先恢复连接并读取权威分类，再提交 Backend action identity。
{
  const current = planningState()
  current.connection = { ...current.connection, status: 'unavailable', lastError: 'Failed to fetch' }
  current.workflow = workflowWithoutInterrupt()
  current.error = '计划执行中断'
  let recoveryOptions: SendWorkflowMessageOptions | undefined
  let recoveryThreadId = ''
  const childWorkflow = {
    ...workflowWithoutInterrupt(),
    runId: 'run-B',
    summary: { status: 'running', phase: 'requirements', message: '继续生成需求' }
  } as WorkflowRunPayload
  let authoritativeReads = 0
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
  h.onRead(async () => {
    authoritativeReads += 1
    if (authoritativeReads > 1) {
      return {
        workflow: childWorkflow,
        lifecycle: authoritativeLifecycle(current, { status: 'running' }),
        recovery: recoveryProjection('thread-A', { classification: 'running', recoveryActionPlan: null })
      }
    }
    return {
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
      message: '回答已保存，可以继续。',
      recoveryActionPlan: {
        schemaVersion: 'recovery-action-plan.v1',
        incidentId: 'incident-AA',
        sourceRunId: 'run-A',
        threadId: 'thread-A',
        executionKind: 'application_planning',
        status: 'recoverable',
        reasonCode: 'INPUT_COMMITTED_EXECUTION_INTERRUPTED',
        message: '回答已保存，可以继续。',
        primaryAction: {
          actionId: 'action-AA',
          kind: 'restart_stage',
          label: '重新执行技术规划',
          description: '从正式技术规划阶段重新执行。',
          requiresConfirmation: false
        },
        alternateActions: []
      }
    })
  }
  })

  await h.runtime.retryCurrentFailure()

  assert.equal(recoveryThreadId, 'thread-A')
  assert.deepEqual(recoveryOptions?.executionRecovery, {
    action: 'execute',
    incidentId: 'incident-AA',
    actionId: 'action-AA'
  })
  assert.equal('sourceRunId' in (recoveryOptions?.executionRecovery || {}), false)
  assert.equal('targetNode' in (recoveryOptions?.executionRecovery || {}), false)
  assert.equal('currentNode' in (recoveryOptions?.executionRecovery || {}), false)
  assert.equal('phase' in (recoveryOptions?.executionRecovery || {}), false)
  assert.equal(recoveryOptions?.applicationPlanningInteraction, undefined)
  assert.equal(recoveryOptions?.workflowDebug, undefined)
  assert.equal(h.calls.length, 0)
  assert.equal(h.current()?.workflow?.runId, 'run-B')
}

// AA1：恢复端点拒绝请求后，即使权威对账成功，当前卡片也必须保留动作错误。
{
  const current = planningState()
  const h = harness(current, {
    createRecoverySession: () => ({
      sendMessage: async () => { throw new Error('HTTP 404: Not Found') }
    })
  })
  h.onRead(async () => ({
    workflow: workflowWithoutInterrupt(),
    lifecycle: authoritativeLifecycle(current),
    recovery: recoveryProjection('thread-A', {
      classification: 'ready_to_continue',
      reasonCode: 'INTERRUPTED_CONTINUE_READY',
      message: '已验证中断执行的最新 checkpoint，可以继续执行。'
    })
  }))

  await h.runtime.retryCurrentFailure()

  assert.match(h.current()?.recoveryActionError || '', /HTTP 404/)
  assert.equal(h.current()?.connection.status, 'healthy')
  assert.equal(h.current()?.recovery?.recoveryActionPlan?.status, 'recoverable')
}

// AA2：Runtime 重建后沿用较高连接代次时，一次点击仍须完成对账并提交恢复动作。
{
  const initial = planningState()
  initial.connection = { ...initial.connection, requestGeneration: 10 }
  let recoveryCalls = 0
  const h = harness(initial, {
    createRecoverySession: () => ({
      sendMessage: async () => {
        recoveryCalls += 1
        return result()
      }
    })
  })
  h.onRead(async () => ({
    workflow: workflowWithoutInterrupt(),
    lifecycle: authoritativeLifecycle(initial),
    recovery: recoveryProjection('thread-A', { classification: 'ready_to_continue' })
  }))

  await h.runtime.retryCurrentFailure()

  assert.equal(recoveryCalls, 1)
  assert.equal(h.current()?.transportState, 'idle')
  assert.equal(h.current()?.connection.status, 'healthy')
  assert.ok(h.current()!.connection.requestGeneration > 10)
  assert.equal(h.current()?.recoveryActionError, undefined)
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

// AC：needs_attention 保留 Retry Entry，只发送当前 source hint 供 Backend 重新判断。
{
  const current = planningState()
  let recoveryOptions: SendWorkflowMessageOptions | undefined
  const needsAttentionPlan = {
    schemaVersion: 'recovery-action-plan.v1' as const,
    incidentId: 'incident-needs-attention',
    sourceRunId: 'run-needs-attention',
    threadId: 'thread-A',
    executionKind: 'application_planning' as const,
    status: 'needs_attention' as const,
    reasonCode: 'NO_RECOVERY_POINT',
    message: '当前现场暂时无法证明安全恢复。',
    primaryAction: null,
    alternateActions: []
  }
  const h = harness(current, {
    createRecoverySession: () => ({
      sendMessage: async (_message, options) => {
        recoveryOptions = options
        return result(workflowWithoutInterrupt())
      }
    })
  })
  h.onRead(async () => ({
    workflow: workflowWithoutInterrupt(),
    lifecycle: authoritativeLifecycle(current, { status: 'failed' }),
    recovery: recoveryProjection('thread-A', {
      classification: 'blocked',
      sourceRunId: 'run-needs-attention',
      reasonCode: 'NO_RECOVERY_POINT',
      message: '当前现场暂时无法证明安全恢复。',
      recoveryActionPlan: needsAttentionPlan
    })
  }))

  await h.runtime.retryCurrentFailure()

  assert.deepEqual(recoveryOptions?.executionRecovery, {
    action: 'retry_current_failure',
    sourceRunId: 'run-needs-attention'
  })
}

// UI worker 中断不伪造 Graph 失败；重试先读取当前确认门，再进入节点已有自愈。
{
  const current = planningState()
  const workflow = confirmationWorkflow()
  workflow.summary.phase = 'ui_confirmation'
  workflow.summary.clarification = { mode: 'ui_design_confirmation', status: 'requires_user_input' }
  workflow.state = { application_planning_interrupt: { gateId: 'ui-current-gate', artifact: 'ui_designs', artifactRevision: 'ui-revision' } }
  let reads = 0
  const h = harness(current)
  h.onRead(async () => ({
    workflow,
    lifecycle: authoritativeLifecycle(current, { stage: 'awaiting_ui_design_confirmation', status: 'awaiting_user' }),
    recovery: recoveryProjection('thread-A', { uiGenerationRecovery: ++reads === 1 ? { pageIds: ['orphan'] } : null })
  }))
  h.onSend(async options => {
    assert.equal(h.readCalls(), 1)
    assert.deepEqual(options.applicationPlanningInteraction?.uiAction, { action: 'refresh' })
    assert.equal(options.applicationPlanningInteraction?.action, 'ui_action')
    assert.equal(options.applicationPlanningInteraction?.gateId, 'ui-current-gate')
    assert.equal(options.executionRecovery, undefined)
    return result(workflow)
  })
  assert.ok(uiDesignRecoveryError(recoveryProjection('thread-A', { uiGenerationRecovery: { pageIds: ['orphan'] } })))
  await h.runtime.retryCurrentFailure()
  assert.equal(h.calls.length, 1)
  assert.equal(h.readCalls(), 2)
  assert.equal(uiDesignRecoveryError(h.current()?.recovery), undefined)
  assert.equal(h.current()?.workflow?.summary.status, 'requires_user_input')
}

// 旧 UI 中断已经结束或 worker 已活跃时，读取最新事实后不发送任何生成或确认动作。
{
  const current = { ...planningState(), recovery: recoveryProjection('thread-A', { uiGenerationRecovery: { pageIds: ['orphan'] } }) }
  const h = harness(current)
  await h.runtime.retryCurrentFailure()
  assert.equal(h.calls.length, 0)
  assert.equal(h.readCalls(), 1)
  assert.equal(uiDesignRecoveryError(h.current()?.recovery), undefined)
}

// 显式重试对账到已完成的模板节点时，续接原事务，不重复生成模板或重放技术规划确认。
{
  const initial = planningState()
  const continuation = { action: 'continue_revision_build' as const, changeId: 'change-ready',
    formalBranch: 'design_stage_revision' as const, token: 'c'.repeat(48), technicalPlanSha256: 'a'.repeat(64) }
  const lifecycle = { ...authoritativeLifecycle(initial), activeFormalRevision: {
    changeId: continuation.changeId, formalBranch: continuation.formalBranch,
    status: 'continuation_ready', technicalPlanSha256: continuation.technicalPlanSha256
  } } as ApplicationLifecycle
  const workflow: WorkflowRunPayload = { runId: 'template-completed', threadId: initial.threadId, events: [],
    summary: { status: 'completed', phase: 'template_reconcile', revisionContinuation: continuation },
    state: { lifecycle }, result: { lifecycle, revision_continuation: continuation } }
  let handoffs = 0
  const h = harness(initial, {
    /** 仅记录服务端已签发的原事务交接。 */
    onRevisionContinuation: async handoff => { handoffs++; assert.equal(handoff.continuation.changeId, continuation.changeId) }
  })
  h.onRead(async () => ({ workflow, lifecycle,
    recovery: recoveryProjection(initial.threadId, { classification: 'completed', sourceRunId: workflow.runId }) }))
  await h.runtime.retryCurrentFailure()
  assert.equal(handoffs, 1)
  assert.equal(h.calls.length, 0)
}

// 技术规划报错后的问题只发布答复，不能把原恢复源替换成一次普通对话。
{
  const initial = planningState()
  initial.workflow = { ...confirmationWorkflow(), summary: { status: 'failed', message: '模型请求失败。' } }
  initial.recovery = recoveryProjection()
  initial.recoveryActionError = '上次提问被限流'
  const h = harness(initial, { readTechnicalPlanningReply: async () => ({
    intent: 'reply', message: '原修改要求仍已保存，可以使用上方重试。',
    planningThreadId: initial.threadId, sourceRunId: 'failed-run', checkpointId: 'checkpoint-A'
  }) })
  await h.runtime.submitClarification(initial.workflow, {}, undefined, undefined, '为什么失败？', true)
  assert.equal(h.calls.length, 0)
  assert.equal(h.current()?.workflow, initial.workflow)
  assert.equal(h.current()?.recovery, initial.recovery)
  assert.equal(h.contents.at(-1), '原修改要求仍已保存，可以使用上方重试。')
  assert.equal(h.current()?.recoveryActionError, undefined)
}

// 明确的新技术修改使用原 Planning 会话，携带后端分类时的当前版本身份。
{
  const initial = planningState()
  initial.workflow = confirmationWorkflow()
  const h = harness(initial, { readTechnicalPlanningReply: async () => ({
    intent: 'revise', message: '将修改已有技术规划。',
    planningThreadId: initial.threadId, sourceRunId: 'source-run', checkpointId: 'checkpoint-B'
  }) })
  const latestRecovery = recoveryProjection(initial.threadId, { sourceRunId: 'new-generation-run', recoveryActionPlan: null })
  h.onRead(async () => ({ workflow: confirmationWorkflow(initial.threadId), lifecycle: initial.lifecycle!, recovery: latestRecovery }))
  await h.runtime.submitClarification(initial.workflow, {}, undefined, undefined, '修改前端架构说明', true)
  assert.equal(h.calls.length, 1)
  assert.equal(h.calls[0].options.conversation, undefined)
  assert.deepEqual(h.calls[0].options.technicalPlanningConversation, {
    action: 'revise', workspaceRoot: initial.application.workspaceRoot,
    applicationId: initial.application.id, planningThreadId: initial.threadId,
    request: '修改前端架构说明', sourceRunId: 'source-run', checkpointId: 'checkpoint-B'
  })
  assert.equal(h.current()?.recovery, latestRecovery)
}

// 上游变更沿原规划线程发起设计意图流程，不用技术节点调试绕过生成与确认链。
{
  const initial = planningState()
  initial.workflow = confirmationWorkflow()
  const h = harness(initial, { readTechnicalPlanningReply: async () => ({
    intent: 'design_change', message: '返回需求设计。',
    planningThreadId: initial.threadId, sourceRunId: 'source-design', checkpointId: 'checkpoint-design'
  }) })
  h.onRead(async () => ({ workflow: confirmationWorkflow(initial.threadId), lifecycle: initial.lifecycle!, recovery: recoveryProjection() }))
  await h.runtime.submitClarification(initial.workflow, {}, undefined, undefined, '@需求设计 增加角色', true)
  assert.equal(h.calls.length, 1)
  assert.deepEqual(h.calls[0].options.technicalPlanningConversation, {
    action: 'revise_design', workspaceRoot: initial.application.workspaceRoot,
    applicationId: initial.application.id, planningThreadId: initial.threadId,
    request: '@需求设计 增加角色', sourceRunId: 'source-design', checkpointId: 'checkpoint-design'
  })
}

// 停止只读提问后，晚到的修改分类不能自动触发生成。
{
  const initial = planningState()
  initial.workflow = confirmationWorkflow()
  let complete!: (reply: { intent: 'revise'; message: string; planningThreadId: string; sourceRunId: string; checkpointId: string }) => void
  const h = harness(initial, { readTechnicalPlanningReply: async () => new Promise(resolve => { complete = resolve }) })
  const pending = h.runtime.submitClarification(initial.workflow, {}, undefined, undefined, '修改架构', true)
  h.runtime.dispose()
  complete({ intent: 'revise', message: '修改', planningThreadId: initial.threadId, sourceRunId: 'old', checkpointId: 'old' })
  await pending
  assert.equal(h.calls.length, 0)
}

// 首次生成尚无 checkpoint 时，只有用户显式重试才重新提交一次生成。
{
  const initial = planningState()
  initial.lifecycle.initialization = { ...initial.lifecycle.initialization, stage: 'collecting_requirement', status: 'pending' }
  const h = harness(initial)
  h.onRead(async () => { return undefined })
  await h.runtime.retryCurrentFailure()
  assert.equal(h.calls.length, 1)
}

// 后端没有给失败状态签发动作时，点击重试必须留下明确错误，不能静默无效。
{
  const initial = planningState()
  const h = harness(initial)
  h.onRead(async () => ({ workflow: confirmationWorkflow(), lifecycle: initial.lifecycle,
    recovery: recoveryProjection('thread-A', { classification: 'blocked', recoveryActionPlan: null }) }))
  await h.runtime.retryCurrentFailure()
  assert.equal(h.calls.length, 0)
  assert.match(h.current()?.recoveryActionError || '', /未提供可执行的重试入口/)
}

console.log('application planning runtime tests passed')

// 停止生成保留当前正式修订，原线程读取后端继续生成动作，不发布结束回执。
{
  const current = planningState()
  current.lifecycle.activeFormalRevision = { changeId: 'keep-change', planningThreadId: current.threadId } as any
  current.workflow = { runId: 'run-A', threadId: current.threadId, events: [], state: { workflow_scope: 'application_planning' }, summary: { status: 'running', phase: 'technical_planning_generate' } }
  const order: string[] = []
  const h = harness(current, {
    /** 后端停止仅返回原修订，不释放它。 */
    stopPlanningExecution: async () => { order.push('backend-stop'); return current.lifecycle },
    /** 停止生成不得调用结束修订动作。 */
    endPlanningExecution: async () => { throw new Error('不能结束本轮修订') }
  })
  h.onStop(async () => { order.push('transport-stop') })
  h.onRead(async () => ({ lifecycle: current.lifecycle, workflow: { ...current.workflow!, summary: { status: 'failed', phase: 'technical_planning_generate' } },
    recovery: recoveryProjection(current.threadId, { classification: 'ready_to_continue' }) }))
  await h.runtime.stop()
  assert.deepEqual(order, ['backend-stop', 'transport-stop'])
  assert.equal(h.current()?.lifecycle.activeFormalRevision?.changeId, 'keep-change')
  assert.equal(h.current()?.recovery?.classification, 'ready_to_continue')
  assert.ok(h.current()?.recovery?.recoveryActionPlan?.primaryAction)
  assert.equal(h.current()?.workflow?.summary.planControl, undefined)
  assert.equal(h.current()?.stopRequested, false)
  assert.equal(h.current()?.transportState, 'idle')
}

// 停止尚未退出时保留重试停止入口，不能提前显示业务继续。
{
  const current = planningState()
  current.lifecycle.activeFormalRevision = { changeId: 'keep-change', planningThreadId: current.threadId } as any
  const h = harness(current, { stopPlanningExecution: async () => { throw new Error('后台仍在退出') } })
  await assert.rejects(h.runtime.stop(), /后台仍在退出/)
  assert.equal(h.current()?.stopRequested, true)
  assert.equal(h.current()?.lifecycle.activeFormalRevision?.changeId, 'keep-change')
  assert.equal(h.current()?.transportState, 'idle')
}
