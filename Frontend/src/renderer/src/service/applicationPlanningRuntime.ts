import type {
  ApplicationPlanningDebugStage,
  ApplicationPlanningConfirmation,
  ApplicationPlanningInteraction,
  WorkflowClarificationAnswers,
  WorkflowDebugOptions,
  WorkflowDesignStageRevisionStart,
  WorkflowRunPayload
} from '../typings'
import {
  AgUiChatSession,
  AgUiRunError,
  getExecutionRecoveryActionUrl,
  type SendWorkflowMessageOptions
} from './agUiAgent'
import {
  applicationPlanningDisplayStatus,
  type ApplicationPlanningCurrentEvent,
  type ApplicationPlanningCurrentState
} from './activeApplicationPlanning'
import {
  buildApplicationPlanningRequest,
  createApplicationPlanningSession,
  revisionContinuationHandoffFromWorkflow,
  saveRequirementSpecDraft,
  type WorkflowRevisionContinuationHandoff
} from './applicationPagePlanning'
import { isAuthenticationFailure } from './authentication'
import { subscribeBackendConnection } from './backendConnection'
import { recoveryStore } from './recoveryStore'
import { normalizeWorkspaceRoot } from './applicationWorkspace'
import { endApplicationPlanningExecution } from './applicationLifecycle'
import { planningWorkflowCanPublishDuringRun, planningWorkflowPhase } from './applicationPlanningWorkflowState'
import {
  readApplicationPlanningAuthoritativeSnapshot,
  type ApplicationPlanningAuthoritativeSnapshot,
  type ApplicationPlanningRecoveryActionPlan
} from './applicationPlanningRecovery'
import {
  buildPlanningInteraction,
  hasPlanningInterrupt,
  MISSING_INTERRUPT_ERROR,
  planningInterruptIdentity,
  planningResumeFrom,
  planningRuntimeError,
  withSavedRequirementSpec,
  workflowConfirmation
} from './applicationPlanningRuntimeHelpers'
import { productConversationSubmissionError } from './applicationPlanningProductConversation'
import { readTechnicalPlanningReply, prepareTechnicalPlanningRevision, type TechnicalPlanningReply } from './technicalPlanningConversation'
import { workflowEndedPlanControl } from '../components/AiChatPanel/planExecutionMode'
import { isWorkflowDebugNodeForStage } from './workflowDebugNodes'
import { stoppedPlanningAcceptsFeedback } from '../components/AiChatPanel/planningStageAccess'

export type ApplicationPlanningRuntimeDependencies = {
  readTechnicalPlanningReply?: typeof readTechnicalPlanningReply
  prepareTechnicalPlanningRevision?: typeof prepareTechnicalPlanningRevision
  applicationId: string
  threadId: string
  getCurrentState: () => ApplicationPlanningCurrentState | undefined
  dispatchCurrentEvent: (event: ApplicationPlanningCurrentEvent) => void
  publishContent: (content: string) => void
  publishReply: (reply: TechnicalPlanningReply) => void
  publishWorkflow: (workflow: WorkflowRunPayload) => void
  onTechnicalPlanConfirmed: (confirmation: ApplicationPlanningConfirmation) => Promise<boolean>
  onRevisionContinuation: (handoff: WorkflowRevisionContinuationHandoff) => Promise<void>
  session?: Pick<AgUiChatSession, 'sendMessage' | 'stop' | 'hasActiveRun'> & Partial<Pick<AgUiChatSession, 'detach'>>
  /** 测试可替换短生命周期 Native Recovery 会话；生产不复用普通 Planning endpoint。 */
  createRecoverySession?: (
    threadId: string
  ) => Pick<AgUiChatSession, 'sendMessage'>
  /** 测试可替换保存服务；生产仍使用原有 AG-UI 保存入口。 */
  saveRequirementSpecDraft?: typeof saveRequirementSpecDraft
  /** 结束控制测试可替换 AG-UI 调用，生产必须完成后端修订收口。 */
  endPlanningExecution?: typeof endApplicationPlanningExecution
  /** 测试可替换权威读取；生产使用独立 applicationPlanningRecovery client。 */
  readAuthoritativeSnapshot?: (
    application: ApplicationPlanningCurrentState['application'],
    threadId: string
  ) => Promise<ApplicationPlanningAuthoritativeSnapshot | undefined>
}

export type PlanningReconcileOutcome =
  | { status: 'recovered' }
  | { status: 'checkpoint_missing' }
  | { status: 'uncertain'; error: string }

type PlanningExecutionFailure = 'authoritative_failure' | 'recovered' | 'uncertain'

const PLANNING_SYNC_ERROR = '与后端连接中断，当前规划状态尚未确认。请重新同步状态。'

/** 标识 Backend 已证明本次乐观提交尚未被 Native Interrupt 消费。 */
export class ApplicationPlanningSubmissionNotCommittedError extends Error {
  /** 保留原错误文案，供 UI 精确回滚本次乐观消息。 */
  constructor(message: string) {
    super(message)
    this.name = 'ApplicationPlanningSubmissionNotCommittedError'
  }
}

/** 独立于 React 的 Planning 执行器；业务事实始终同步读取 Canonical State。 */
export class ApplicationPlanningRuntime {
  readonly applicationId: string
  readonly threadId: string
  readonly workspaceRoot: string
  private readonly session: NonNullable<ApplicationPlanningRuntimeDependencies['session']>
  private started = false
  private completed = false
  private disposed = false
  private runToken = 0
  private planningEndAcknowledgement?: { sourceToken: number; result: Promise<boolean> }
  private runActive = false
  private reconcilePromise?: Promise<PlanningReconcileOutcome>
  private streamingContent = ''
  private technicalMessageAbort?: AbortController
  private streamingListeners = new Set<(content: string) => void>()
  private unsubscribeBackendConnection: () => void

  /** 绑定应用和线程身份，只保存会话执行能力，不捕获业务快照。 */
  constructor(private readonly dependencies: ApplicationPlanningRuntimeDependencies) {
    this.applicationId = dependencies.applicationId
    this.threadId = dependencies.threadId
    this.workspaceRoot = normalizeWorkspaceRoot(dependencies.getCurrentState()?.application.workspaceRoot)
    this.session = dependencies.session ?? createApplicationPlanningSession(this.threadId)
    this.unsubscribeBackendConnection = subscribeBackendConnection((connection) => {
      const current = this.dependencies.getCurrentState()
      if (this.disposed || current?.threadId !== this.threadId || current.application.id !== this.applicationId || normalizeWorkspaceRoot(current.application.workspaceRoot) !== this.workspaceRoot) return
      this.runToken = Math.max(this.runToken, current.connection.requestGeneration) + 1
      this.runActive = false
      this.reconcilePromise = undefined
      this.technicalMessageAbort?.abort()
      this.session.detach?.()
      this.dispatch({ type: 'reconcile_failed', applicationId: this.applicationId, threadId: this.threadId,
        requestGeneration: this.runToken, error: '后台连接正在恢复，请稍候。' })
      if (connection.status === 'ready') void this.reconcileCurrentState(true)
    })
  }

  /** 交接响应已包含权威 checkpoint，目标实例无需再执行冷启动读取或自动启动。 */
  acceptStageHandoff(): void {
    const current = this.requireCurrentState()
    if (current.workflow?.threadId !== this.threadId) throw new Error('阶段交接缺少目标线程快照。')
    this.started = true
  }

  /** 在每次动作时检查当前身份，已删除或换线程的 Runtime 不得继续执行。 */
  private requireCurrentState(): ApplicationPlanningCurrentState {
    const state = this.dependencies.getCurrentState()
    if (
      this.disposed ||
      !state ||
      state.application.id !== this.applicationId ||
      normalizeWorkspaceRoot(state.application.workspaceRoot) !== this.workspaceRoot ||
      state.threadId !== this.threadId
    ) {
      throw new Error('当前 Planning Runtime 已失效。')
    }
    return state
  }

  /** 静默丢弃过期运行和已失效实例的异步回调。 */
  private isCurrentRun(token: number): boolean {
    const current = this.dependencies.getCurrentState()
    return (
      !this.disposed &&
      token === this.runToken &&
      current?.application.id === this.applicationId &&
      normalizeWorkspaceRoot(current.application.workspaceRoot) === this.workspaceRoot &&
      current.threadId === this.threadId
    )
  }

  /** 通过唯一事件入口提交当前实例的状态变化。 */
  private dispatch(event: ApplicationPlanningCurrentEvent): void {
    const current = this.requireCurrentState()
    const key = recoveryStore.key(current.application.workspaceRoot || '', this.applicationId, this.threadId)
    if (event.type === 'run_started' || event.type === 'planning_end_started') recoveryStore.invalidate(key)
    this.dependencies.dispatchCurrentEvent({ ...event, workspaceRoot: this.workspaceRoot })
    const next = this.dependencies.getCurrentState()
    if (next?.threadId === this.threadId) recoveryStore.adopt(key, next.lifecycle)
  }

  /** 将显式恢复动作的失败留在当前 Incident 上，避免对账成功后吞掉请求错误。 */
  private reportRecoveryActionFailure(reason: unknown, fallback: string): void {
    const current = this.dependencies.getCurrentState()
    if (this.disposed || current?.application.id !== this.applicationId || current.threadId !== this.threadId) return
    this.dispatch({
      type: 'recovery_action_failed',
      applicationId: this.applicationId,
      threadId: this.threadId,
      error: planningRuntimeError(reason, fallback)
    })
  }

  /** 判断 lifecycle 是否仍是唯一允许在 checkpoint 缺失时首次启动 Graph 的初始状态。 */
  private isFreshInitialState(state: ApplicationPlanningCurrentState): boolean {
    return (
      state.lifecycle.initialization.stage === 'collecting_requirement' &&
      state.lifecycle.initialization.status === 'pending'
    )
  }

  /** 先提交实时帧，再同步读取 reducer 合并结果供聊天历史投递。 */
  private applyWorkflow(workflow: WorkflowRunPayload): WorkflowRunPayload | undefined {
    if (workflow.threadId !== this.threadId) return undefined
    this.dispatch({
      type: 'workflow_received',
      applicationId: this.applicationId,
      threadId: this.threadId,
      workflow
    })
    return this.requireCurrentState().workflow
  }

  /** 更新临时流式正文；正文不进入 Canonical Planning Current State。 */
  private setStreamingContent(content: string): void {
    this.streamingContent = content
    for (const listener of this.streamingListeners) listener(content)
  }

  /** 为新订阅者立即提供当前正文，并返回独立取消函数。 */
  subscribeStreamingContent(listener: (content: string) => void): () => void {
    this.requireCurrentState()
    this.streamingListeners.add(listener)
    listener(this.streamingContent)
    return () => {
      this.streamingListeners.delete(listener)
    }
  }

  /** 每个实例只启动一次；模板阶段和停止态不自动运行 Graph。 */
  async ensureStarted(): Promise<void> {
    if (this.disposed) return
    let current = this.requireCurrentState()
    if (this.started) return
    this.started = true
    if (current.restoreArtifactsFromDisk) {
      const outcome = await this.reconcileCurrentState()
      current = this.requireCurrentState()
      if (outcome.status === 'uncertain') return
      if (outcome.status === 'checkpoint_missing') {
        if (!this.isFreshInitialState(current)) return
        await this.runPlanning(buildApplicationPlanningRequest(current.application))
        return
      }
      // 已有 checkpoint 的冷启动只读对账：仍运行就展示现状，中断则等待兜底动作。
      // ActionPlan 尚未签发也不能落入普通阶段启动，否则会抢在孤儿识别前重复生成。
      return
    }
    if (
      [
        'generating_application_template_files',
        'application_template_generation_failed',
        'ready_for_workbench'
      ].includes(current.lifecycle.initialization.stage)
    )
      return
    if (current.lifecycle.initialization.status === 'awaiting_user') {
      // 新迭代发起时前端标记 awaiting_user 但后端 checkpoint 尚未建立，
      // 跳过 reconcile 避免误报"规划状态尚未同步"，等用户输入需求后再触发。
      //
      // 判据必须带上 stage：后端写盘的 lifecycle 是 pending，只有前端
      // handleConfirmIteration 在内存里把它标成 awaiting_user（与 utils.ts 的
      // shouldInjectPlanningPlaceholder 同一约定）。只按 status + 无 workflow 判，
      // 会把「恢复既有会话、尚未产出 workflow」这一正常情形一起拦掉。
      const awaitingIterationRequest =
        !current.restoreArtifactsFromDisk &&
        !current.workflow &&
        current.lifecycle.initialization.stage === 'collecting_requirement'
      if (awaitingIterationRequest) return
      await this.reconcileCurrentState()
      return
    }
    if (applicationPlanningDisplayStatus(current) !== 'running') return
    await this.runPlanning(
      current.workflow
        ? '请从上次保存的规划状态继续执行。'
        : buildApplicationPlanningRequest(current.application)
    )
  }

  /** 先对账，再提交 Backend 签发的恢复动作；缺少 action identity 时保持 fail closed。 */
  async retryCurrentFailure(restartInitialPlanning = false): Promise<void> {
    this.dispatch({
      type: 'recovery_action_started',
      applicationId: this.applicationId,
      threadId: this.threadId
    })
    try {
      const outcome = await this.reconcileCurrentState()
      // 首次生成尚未创建 checkpoint 时，显式重试可以重新发起；已有产物仍只走后端恢复合同。
      if (outcome.status === 'checkpoint_missing' && this.isFreshInitialState(this.requireCurrentState())) {
        await this.runPlanning(buildApplicationPlanningRequest(this.requireCurrentState().application))
        return
      }
      if (outcome.status !== 'recovered') return
      const current = this.requireCurrentState()
      if (restartInitialPlanning) {
        // 工作区变化后只接受明确的新生成动作；原继续入口仍须保持指纹一致。
        const stopped = current.lifecycle.endedPlanningExecution
        if (!stopped?.canRestart || stopped.threadId !== this.threadId)
          throw new Error('当前没有可重新生成的首次创建任务，请同步当前流程。')
        await this.executeRecoveryRetry({ threadId: this.threadId, sourceRunId: stopped.runId }, true)
        return
      }
      const recovery = current.recovery
      // 重试对账发现同一规划已完成时，只消费服务端已签发的原续接，不重新执行模板节点。
      if (current.workflow?.summary.status === 'completed' && revisionContinuationHandoffFromWorkflow(current.workflow)) {
        await this.handlePlanningResult(this.runToken, current.workflow)
        return
      }
      // 孤立生成页只重读确认门；生成必须由用户在对应页面显式提交。
      if (recovery?.classification === 'awaiting_user' && recovery.uiGenerationRecovery?.pageIds.length && current.workflow) {
        await this.submitClarification(current.workflow, {
          ui_design_action: { action: 'refresh' },
          __applicationPlanningAction: 'ui_action'
        })
        await this.reconcileCurrentState()
        return
      }
      const plan = recovery?.recoveryActionPlan
      if (!plan) {
        if (['awaiting_user', 'running', 'completed'].includes(recovery?.classification || '')) return
        throw new Error('规划状态异常：后端未提供可执行的重试入口，请检查当前规划状态。')
      }
      if (plan.status === 'needs_attention') {
        await this.executeRecoveryRetry(plan)
        return
      }
      if (plan.status !== 'recoverable' || !plan.primaryAction) return
      await this.executeRecoveryAction(plan)
    } catch (reason) {
      this.reportRecoveryActionFailure(reason, '继续执行中断的规划失败')
    }
  }

  /** 新迭代发起后用户输入需求，用用户输入的消息启动 planning workflow。 */
  async startIterationPlanning(
    userRequest: string,
    workflowDebug?: WorkflowDebugOptions,
    debugStage?: ApplicationPlanningDebugStage
  ): Promise<void> {
    const current = this.requireCurrentState()
    const request = userRequest.trim() || buildApplicationPlanningRequest(current.application)
    if (workflowDebug?.enabled) {
      const resumeFrom = workflowDebug.resumeFrom?.trim() || ''
      const supportedStage = debugStage === 'design' || debugStage === 'planning'
      if (!supportedStage || !isWorkflowDebugNodeForStage(debugStage, resumeFrom)) {
        throw new Error('调试节点与当前阶段不匹配，请重新选择节点。')
      }
      if (workflowConfirmation(current.workflow)) {
        throw new Error('当前规划有待确认事项，请先完成确认后再调试节点。')
      }
      await this.runPlanning(request, undefined, undefined, undefined, workflowDebug)
      return
    }
    if (workflowConfirmation(current.workflow)) return
    await this.runPlanning(request)
  }

  /** 通过独立 AG-UI 动作重试失败的 Template Reconcile，不伪造技术规划恢复。 */
  async retryTemplateReconcile(): Promise<void> {
    const outcome = await this.reconcileCurrentState()
    if (outcome.status !== 'recovered') return
    const current = this.requireCurrentState()
    if (current.lifecycle.activeFormalRevision?.status !== 'template_reconcile_failed') return
    await this.runPlanning(
      buildApplicationPlanningRequest(current.application),
      undefined,
      undefined,
      'retry_template_reconcile'
    )
  }

  /** 正式设计或技术修订统一使用原 Planning Runtime 更新当前规划并发送用户请求。 */
  async startDesignRevision(input: WorkflowDesignStageRevisionStart): Promise<void> {
    await this.runPlanning(input.request, undefined, input)
  }

  /** 确认操作优先使用当前快照，只把调用方卡片用于线程有效性检查。 */
  async submitClarification(
    workflow: WorkflowRunPayload,
    answers: WorkflowClarificationAnswers,
    editedRequirementSpec?: Record<string, unknown>,
    requirementSpecFeedback?: string,
    designChangeRequest?: string,
    technicalPlanningConversation = false,
    designThreadHandoff?: () => Promise<void>,
    conversationTarget?: ApplicationPlanningInteraction['conversationTarget']
  ): Promise<void> {
    const stopped = this.requireCurrentState().lifecycle.endedPlanningExecution
    if (designChangeRequest?.trim() && stoppedPlanningAcceptsFeedback(stopped) &&
        stopped?.threadId === this.threadId &&
        (!technicalPlanningConversation || !conversationTarget || conversationTarget === 'technical_planning')) {
      if (workflow.threadId !== this.threadId) throw new Error('当前规划会话已经失效。')
      if (this.runActive || this.session.hasActiveRun()) throw new Error('当前规划正在执行，请等待完成后提交。')
      // 已结束的旧审阅门没有提交权限；正式修订沿原继续动作恢复其修订上下文再生成。
      await this.executeRecoveryRetry({ threadId: this.threadId, sourceRunId: stopped.runId }, stopped.canRestart, {
        request: designChangeRequest.trim(), ...(conversationTarget ? { conversationTarget } : {})
      })
      return
    }
    if (technicalPlanningConversation && designChangeRequest?.trim()) {
      await this.submitTechnicalPlanningMessage(designChangeRequest.trim(), designThreadHandoff, false, conversationTarget)
      return
    }
    let current = this.requireCurrentState()
    if (workflow.threadId !== current.threadId) throw new Error('当前规划确认卡已经失效。')
    const cardRevision = answers.__applicationPlanningAction === 'revise' &&
      typeof answers.technical_plan_confirmation === 'string'
      ? answers.technical_plan_confirmation.trim() : ''
    if (cardRevision && workflowEndedPlanControl(current.workflow)) {
      // 停止卡与正常卡提交相同的明确技术修改；旧确认已结束，先读取当前版本再进入原生成链路。
      await this.submitTechnicalPlanningMessage(cardRevision, undefined, true)
      return
    }
    if ((this.runActive || this.session.hasActiveRun()) && designChangeRequest?.trim()) {
      throw new Error('当前设计正在生成，完成后即可发送新的调整。')
    }
    if (Object.keys(answers).length === 0 && !designChangeRequest?.trim()) {
      await this.reconcileCurrentState()
      return
    }
    if (!current.workflow || !hasPlanningInterrupt(current.workflow)) {
      const outcome = await this.reconcileCurrentState()
      if (outcome.status !== 'recovered') throw new Error(MISSING_INTERRUPT_ERROR)
      current = this.requireCurrentState()
    }
    const submittable = current.workflow
    if (!submittable || !hasPlanningInterrupt(submittable)) throw new Error(MISSING_INTERRUPT_ERROR)
    const submittedGateIdentity = planningInterruptIdentity(submittable)
    let submitToken: number | undefined
    try {
      await this.withExclusiveTransport(
        async (token) => {
          const interaction = buildPlanningInteraction(
            submittable,
            answers,
            editedRequirementSpec,
            requirementSpecFeedback,
            designChangeRequest,
            conversationTarget
          )
          const latest = this.requireCurrentState()
          const merged = await this.sendMessageWithinTransport(
            token,
            designChangeRequest?.trim() || '请根据本轮确认继续创建规划。',
            this.planningRunOptions(latest, interaction)
          )
          await this.handlePlanningResult(token, merged)
        },
        {
          stopPrevious: true,
          onToken: (token) => {
            submitToken = token
          }
        }
      )
    } catch (reason) {
      if (submitToken === undefined || !this.isCurrentRun(submitToken)) {
        const acknowledgement = this.planningEndAcknowledgement
        // 已确认的主动停止/结束属于正常收口，不把原确认请求的传输取消误报为交接失败。
        if (submitToken !== undefined && acknowledgement?.sourceToken === submitToken && await acknowledgement.result) return
        throw reason
      }
      const outcome = await this.handleExecutionFailure(
        reason,
        designChangeRequest
          ? productConversationSubmissionError(reason)
          : planningRuntimeError(reason, '创建规划确认失败')
      )
      const latest = this.requireCurrentState()
      if (outcome === 'recovered' && latest.recovery?.inputCommitted) return
      if (
        outcome === 'recovered' &&
        planningInterruptIdentity(latest.workflow) !== submittedGateIdentity
      ) {
        return
      }
      if (
        outcome === 'recovered' &&
        latest.recovery?.classification === 'awaiting_user' &&
        planningInterruptIdentity(latest.workflow) === submittedGateIdentity
      ) {
        throw new ApplicationPlanningSubmissionNotCommittedError(
          planningRuntimeError(reason, '创建规划确认失败')
        )
      }
      throw reason
    }
  }

  /** 保存需求草稿后更新当前快照，提示消息由调用它的 UI 决定。 */
  async saveRequirementSpec(
    spec: Record<string, unknown>
  ): Promise<Awaited<ReturnType<typeof saveRequirementSpecDraft>>> {
    const current = this.requireCurrentState()
    const save = this.dependencies.saveRequirementSpecDraft ?? saveRequirementSpecDraft
    const saved = await save(current.application.workspaceRoot || '', spec, current.threadId)
    const latest = this.requireCurrentState().workflow
    if (latest) this.applyWorkflow(withSavedRequirementSpec(latest, saved))
    return saved
  }

  /** 用户停止即结束本轮任务，后端确认退出和清锁后才发布结束回执。 */
  async stop(): Promise<void> {
    const current = this.requireCurrentState()
    this.technicalMessageAbort?.abort()
    let acknowledgeEnd!: (ended: boolean) => void
    this.planningEndAcknowledgement = {
      sourceToken: this.runToken,
      result: new Promise<boolean>((resolve) => { acknowledgeEnd = resolve })
    }
    // 先使旧流失效，后台取消引起的迟到帧不能覆盖结束回执。
    const token = ++this.runToken
    this.dispatch({ type: 'planning_end_started', applicationId: this.applicationId, threadId: this.threadId })
    try {
      const end = this.dependencies.endPlanningExecution ?? endApplicationPlanningExecution
      const lifecycle = await end(current.application, this.threadId)
      // 服务端已确认真实退出；旧 transport 的收口异常不撤销成功的结束。
      await this.session.stop().catch(() => undefined)
      if (!this.isCurrentRun(token)) return
      const message = '本轮任务已结束，已有文件已保留。'
      const workflow: WorkflowRunPayload = {
        runId: current.workflow?.runId || current.lifecycle.activeRunId || 'planning-ended',
        threadId: this.threadId, events: [], state: { workflow_scope: 'application_planning' },
        summary: { status: 'completed', phase: 'plan_control', message, lifecycle,
          planControl: { action: 'end', status: 'ended', targetRunId: current.workflow?.runId || current.lifecycle.activeRunId } }
      }
      this.runActive = false
      this.dispatch({ type: 'planning_ended', applicationId: this.applicationId, threadId: this.threadId, lifecycle, workflow })
      this.dependencies.publishWorkflow(workflow)
      acknowledgeEnd(true)
    } catch (reason) {
      this.reportRecoveryActionFailure(reason, '结束任务失败，请重试结束任务。')
      throw reason
    } finally {
      acknowledgeEnd(false)
      if (this.isCurrentRun(token)) {
        this.runActive = false
        this.dispatch({ type: 'run_settled', applicationId: this.applicationId, threadId: this.threadId })
      }
    }
  }

  /** 显式目标沿指定节点执行；无目标时识别问答或修改，保留原生成与确认链路。 */
  private async submitTechnicalPlanningMessage(
    request: string, designThreadHandoff?: () => Promise<void>, explicitRevision = false,
    conversationTarget?: ApplicationPlanningInteraction['conversationTarget']
  ): Promise<void> {
    const abort = new AbortController()
    let revised = false
    let returnToDesign = false
    let submissionToken: number | undefined
    this.technicalMessageAbort = abort
    try {
      await this.withExclusiveTransport(async (token) => {
        const current = this.requireCurrentState()
        this.dispatch({ type: 'recovery_action_started', applicationId: this.applicationId, threadId: this.threadId })
        const reply = explicitRevision
          ? await (this.dependencies.prepareTechnicalPlanningRevision ?? prepareTechnicalPlanningRevision)(
            current.application, this.threadId, request, abort.signal)
          : await (this.dependencies.readTechnicalPlanningReply ?? readTechnicalPlanningReply)(
            current.application, this.threadId, request, abort.signal, conversationTarget)
        if (abort.signal.aborted || !this.isCurrentRun(token)) return
        this.technicalMessageAbort = undefined
        if (reply.intent === 'reply') {
          this.setStreamingContent(reply.message)
          this.dependencies.publishContent(reply.message)
          this.dependencies.publishReply(reply)
          return
        }
        if (reply.intent === 'design_change') {
          if (!designThreadHandoff) throw new Error('返回需求阶段需要先建立独立会话。')
          // 分类结束并释放原 transport 后再交接，避免旧 Runtime 的 finally 覆盖新线程状态。
          returnToDesign = true
          return
        }
        revised = true
        const merged = await this.sendMessageWithinTransport(token, request, {
          editorMode: 'frontend', workspaceRoot: current.application.workspaceRoot,
          technicalPlanningConversation: {
            action: 'revise', workspaceRoot: current.application.workspaceRoot || '',
            applicationId: this.applicationId, planningThreadId: this.threadId, request,
            sourceRunId: reply.sourceRunId, checkpointId: reply.checkpointId,
            ...(conversationTarget ? { conversationTarget } : {})
          }
        })
        await this.handlePlanningResult(token, merged)
      }, { onToken: (token) => { submissionToken = token } })
      // End 或线程切换已经接管后，旧请求即使正常返回也不能再发起对账抢走新的代次。
      if (submissionToken !== undefined && !this.isCurrentRun(submissionToken)) return
      if (returnToDesign && designThreadHandoff) {
        await designThreadHandoff()
        return
      }
      // 新生成已替换原执行，必须读取 durable truth 撤下旧恢复入口；只读问答不对账。
      if (revised && !this.disposed) await this.reconcileCurrentState()
    } catch (reason) {
      // 服务端停止旧生成流通常表现为 network error；仅当前请求可以发布失败或启动恢复。
      if (abort.signal.aborted || (submissionToken !== undefined && !this.isCurrentRun(submissionToken))) return
      if (revised && !this.disposed) await this.handleExecutionFailure(reason, '技术规划修改失败')
      this.reportRecoveryActionFailure(reason, '技术规划对话失败')
      throw reason
    } finally {
      if (this.technicalMessageAbort === abort) this.technicalMessageAbort = undefined
    }
  }

  /** 以 single-flight 方式读取并原子提交当前 application planning 权威快照。 */
  async reconcileCurrentState(background = false): Promise<PlanningReconcileOutcome> {
    if (this.reconcilePromise) return this.reconcilePromise
    this.reconcilePromise = this.performReconcile(background)
    try {
      return await this.reconcilePromise
    } finally {
      this.reconcilePromise = undefined
    }
  }

  /** 冷恢复及断线后持续只读对账；失败退避不依赖已有 Run，也不自动重跑节点。 */
  async refreshDetachedRunningState(): Promise<void> {
    if (this.disposed || this.runActive || this.session.hasActiveRun()) return
    const current = this.dependencies.getCurrentState()
    if (
      current?.application.id !== this.applicationId || current.threadId !== this.threadId ||
      current.transportState !== 'idle' || !recoveryStore.canRead(recoveryStore.key(current.application.workspaceRoot || '', this.applicationId, this.threadId)) ||
      (current.connection.status !== 'unavailable' && !current.syncError &&
        current.recovery?.classification !== 'running' && planningWorkflowPhase(current.workflow) !== 'ui_confirmation')
    ) return
    // 第一次读取可能早于孤儿租约到期；后续只读对账负责显露兜底入口。
    await this.reconcileCurrentState(true)
  }

  /** 执行一次只读对账，失败只更新 Connection State，不伪造业务 Recovery。 */
  private async performReconcile(background: boolean): Promise<PlanningReconcileOutcome> {
    const current = this.requireCurrentState()
    if (this.runActive) throw new Error('当前 Planning write transport 尚未结束。')
    // Runtime 重建后本地 token 从零开始，但 Canonical State 保留上一实例的连接代次。
    // 新对账必须越过该代次，否则 reducer 会丢弃权威结果并一直停在 reconciling。
    this.runToken = Math.max(this.runToken, current.connection.requestGeneration)
    const token = ++this.runToken
    // 后台健康读取不切换成「正在重连」，避免每五秒闪现浮层、禁用正常确认操作。
    if (!background) this.dispatch({
      type: 'reconcile_started',
      applicationId: this.applicationId,
      threadId: this.threadId,
      requestGeneration: token
    })
    try {
      const read =
        this.dependencies.readAuthoritativeSnapshot ?? readApplicationPlanningAuthoritativeSnapshot
      const snapshot = await read(current.application, this.threadId)
      if (!this.isCurrentRun(token)) throw new Error('当前 Planning Runtime 已失效。')
      if (this.dependencies.readAuthoritativeSnapshot) recoveryStore.recordSuccess(
        recoveryStore.key(current.application.workspaceRoot || '', this.applicationId, this.threadId))
      if (!snapshot) {
        this.dispatch({ type: 'reconcile_checkpoint_missing', applicationId: this.applicationId,
          threadId: this.threadId, requestGeneration: token })
        return { status: 'checkpoint_missing' }
      }
      if (
        snapshot.workflow.threadId !== this.threadId ||
        snapshot.lifecycle.application.id !== this.applicationId
      ) {
        throw new Error('应用规划权威快照身份不匹配。')
      }
      this.dispatch({
        type: 'reconcile_received',
        applicationId: this.applicationId,
        threadId: this.threadId,
        lifecycle: snapshot.lifecycle,
        workflow: snapshot.workflow,
        recovery: snapshot.recovery,
        requestGeneration: token
      })
      const workflow = this.requireCurrentState().workflow
      if (workflow) this.dependencies.publishWorkflow(workflow)
      const output = snapshot.output
      if (workflowEndedPlanControl(workflow)) this.dependencies.publishContent(workflow?.summary.message || '本轮任务已结束，已有文件已保留。')
      else if (output?.runId === workflow?.runId && output?.content) this.dependencies.publishContent(output.content)
      return { status: 'recovered' }
    } catch (reason) {
      if (!this.isCurrentRun(token)) {
        return { status: 'uncertain', error: '当前 Planning Runtime 已失效。' }
      }
      const error = planningRuntimeError(reason, PLANNING_SYNC_ERROR)
      // 只限制后台重读频率；用户显式同步仍可立即尝试，认证失败交给登录门禁。
      if (this.dependencies.readAuthoritativeSnapshot) recoveryStore.recordFailure(
        recoveryStore.key(current.application.workspaceRoot || '', this.applicationId, this.threadId), reason)
      this.dispatch({
        type: 'reconcile_failed',
        applicationId: this.applicationId,
        threadId: this.threadId,
        error,
        requestGeneration: token
      })
      return { status: 'uncertain', error }
    }
  }

  /** 把普通 network/SSE/finalization 异常转为一次只读权威对账。 */
  private async reconcileAfterTransportFailure(
    _reason: unknown
  ): Promise<PlanningExecutionFailure> {
    void _reason
    const outcome = await this.reconcileCurrentState()
    if (outcome.status === 'recovered') return 'recovered'
    if (outcome.status === 'checkpoint_missing') return 'recovered'
    return 'uncertain'
  }

  /** 只使用 Backend action identity 执行 Planning Recovery，不重发用户答案。 */
  private async executeRecoveryAction(
    plan: ApplicationPlanningRecoveryActionPlan
  ): Promise<void> {
    if (plan.status !== 'recoverable' || !plan.primaryAction) return
    const primaryAction = plan.primaryAction
    let recoveryToken: number | undefined
    try {
      await this.withExclusiveTransport(
        async (token) => {
          const current = this.requireCurrentState()
          const session = this.dependencies.createRecoverySession
            ? this.dependencies.createRecoverySession(plan.threadId)
            : new AgUiChatSession(plan.threadId, getExecutionRecoveryActionUrl())
          const merged = await this.sendMessageWithinTransport(
            token,
            primaryAction.label,
            {
              editorMode: 'frontend',
              workspaceRoot: current.application.workspaceRoot,
              executionRecovery: {
                action: 'execute',
                incidentId: plan.incidentId,
                actionId: primaryAction.actionId
              }
            },
            session
          )
          await this.handlePlanningResult(token, merged)
        },
        { onToken: (token) => { recoveryToken = token } }
      )
      // Recovery action 成功只代表请求完成；继续读取 durable truth 后才允许替换旧 Incident。
      await this.reconcileCurrentState()
    } catch (reason) {
      if (recoveryToken !== undefined && !this.isCurrentRun(recoveryToken)) return
      try {
        if (recoveryToken !== undefined) {
          await this.handleExecutionFailure(
            reason,
            planningRuntimeError(reason, '继续执行中断的规划失败')
          )
        }
      } finally {
        this.reportRecoveryActionFailure(reason, '继续执行中断的规划失败')
      }
    }
  }

  /** 对无法直接恢复的当前失败提交 source hint，由 Backend 重新判断安全入口。 */
  private async executeRecoveryRetry(
    plan: Pick<ApplicationPlanningRecoveryActionPlan, 'threadId' | 'sourceRunId'>,
    restartInitialPlanning = false,
    feedback?: { request: string; conversationTarget?: ApplicationPlanningInteraction['conversationTarget'] }
  ): Promise<void> {
    let recoveryToken: number | undefined
    try {
      await this.withExclusiveTransport(
        async (token) => {
          const current = this.requireCurrentState()
          const session = this.dependencies.createRecoverySession
            ? this.dependencies.createRecoverySession(plan.threadId)
            : new AgUiChatSession(plan.threadId, getExecutionRecoveryActionUrl())
          const merged = await this.sendMessageWithinTransport(
            token,
            feedback?.request || (restartInitialPlanning ? '按当前文件重新生成' : '重试'),
            {
              editorMode: 'frontend',
              workspaceRoot: current.application.workspaceRoot,
              executionRecovery: {
                action: restartInitialPlanning ? 'restart_initial_planning' : feedback ? 'continue' : 'retry_current_failure',
                sourceRunId: plan.sourceRunId,
                ...(feedback ? { feedback } : {})
              }
            },
            session
          )
          await this.handlePlanningResult(token, merged)
        },
        { onToken: (token) => { recoveryToken = token } }
      )
      await this.reconcileCurrentState()
    } catch (reason) {
      if (recoveryToken !== undefined && !this.isCurrentRun(recoveryToken)) return
      try {
        if (recoveryToken !== undefined) {
          await this.handleExecutionFailure(reason, planningRuntimeError(reason, '重新检查当前失败时出错'))
        }
      } finally {
        this.reportRecoveryActionFailure(reason, '重新检查当前失败时出错')
      }
      // 新意见提交失败需恢复 Composer 草稿；既有按钮重试继续原位展示错误。
      if (feedback) throw reason
    }
  }

  /** 区分认证、服务端明确 RUN_ERROR 与普通 transport uncertainty。 */
  private async handleExecutionFailure(
    reason: unknown,
    fallback: string
  ): Promise<PlanningExecutionFailure> {
    if (isAuthenticationFailure(reason)) return 'authoritative_failure'
    if (reason instanceof AgUiRunError) {
      const workflow = reason.workflow ? this.applyWorkflow(reason.workflow) : undefined
      this.dispatch({
        type: 'run_failed',
        applicationId: this.applicationId,
        threadId: this.threadId,
        error: reason.message || fallback,
        workflow
      })
      // authoritative RUN_ERROR 已完成 durable 终态写入，释放 write transport 后重新读取当前 lineage head。
      await this.reconcileCurrentState()
      return 'authoritative_failure'
    }
    // stopPrevious 或主 SSE 失败后旧 token 必须立即失效；后端共享 barrier 负责等待 writer。
    this.runActive = false
    return this.reconcileAfterTransportFailure(reason)
  }

  /** 使晚到帧立即失效并释放会话与正文订阅，停止失败不会写回已移除的应用。 */
  dispose(): void {
    if (this.disposed) return
    this.technicalMessageAbort?.abort()
    this.disposed = true
    this.unsubscribeBackendConnection()
    this.runToken += 1
    this.runActive = false
    this.streamingListeners.clear()
    this.streamingContent = ''
    this.session.detach?.()
  }

  /** 初始生成、节点调试和卡片恢复共用同一个带代次保护的运行入口。 */
  private async runPlanning(
    messageText: string,
    interaction?: ApplicationPlanningInteraction,
    designRevision?: WorkflowDesignStageRevisionStart,
    workflowAction?: SendWorkflowMessageOptions['workflowAction'],
    workflowDebugOverride?: WorkflowDebugOptions
  ): Promise<void> {
    const current = this.requireCurrentState()
    if (!current.application.workspaceRoot) return
    const previousRunActive = this.runActive || this.session.hasActiveRun()
    if (previousRunActive && !interaction && !designRevision) return
    if (previousRunActive && interaction?.action === 'design_change')
      throw new Error('当前设计正在生成，完成后即可发送新的调整。')
    let token: number | undefined
    try {
      await this.withExclusiveTransport(
        async (currentToken) => {
          const latest = this.requireCurrentState()
          const merged = await this.sendMessageWithinTransport(
            currentToken,
            messageText,
            this.planningRunOptions(
              latest,
              interaction,
              designRevision,
              workflowAction,
              workflowDebugOverride
            )
          )
          // 入场拒绝也有完整 Workflow 终态；必须撤销未成功的交接，不能视为已转入设计。
          if (designRevision && merged?.summary.status === 'failed') {
            throw new AgUiRunError(merged.summary.message || '返回设计阶段失败，请重新确认。', {
              workflow: merged, toolCalls: [], processSteps: []
            })
          }
          await this.handlePlanningResult(currentToken, merged)
        },
        {
          stopPrevious: Boolean(interaction || designRevision),
          onToken: (currentToken) => {
            token = currentToken
          }
        }
      )
    } catch (reason) {
      if (token === undefined || !this.isCurrentRun(token)) {
        const end = this.planningEndAcknowledgement
        // 主动结束成功属于正常收口，不能让来源会话把取消误报成返回设计失败。
        if (token !== undefined && end?.sourceToken === token && await end.result) return
        if (interaction || designRevision || workflowAction) throw reason
        return
      }
      const outcome = await this.handleExecutionFailure(
        reason,
        planningRuntimeError(reason, '创建规划运行失败')
      )
      // 断线只证明本轮 transport 结束，不能证明正式修订尚未进入。
      // 保留已准备的设计会话和来源回执，重连后通过权威对账恢复原 Graph。
      const activeRevision = this.requireCurrentState().lifecycle.activeFormalRevision
      const designRevisionEntered = designRevision && activeRevision &&
        activeRevision.impactInteractionId === designRevision.impact.interactionId &&
        activeRevision.sourceThreadId === designRevision.sourceConversationThreadId &&
        activeRevision.sourceRunId === designRevision.sourceRunId &&
        activeRevision.planningThreadId === this.threadId
      if (designRevision && (outcome === 'uncertain' || designRevisionEntered)) return
      if ((interaction || designRevision || workflowAction) && outcome !== 'recovered') throw reason
    }
  }

  /** 构造普通 Planning 请求选项，始终从调用瞬间的 Canonical State 读取业务事实。 */
  private planningRunOptions(
    current: ApplicationPlanningCurrentState,
    interaction?: ApplicationPlanningInteraction,
    designRevision?: WorkflowDesignStageRevisionStart,
    workflowAction?: SendWorkflowMessageOptions['workflowAction'],
    workflowDebugOverride?: WorkflowDebugOptions
  ): SendWorkflowMessageOptions {
    return {
      application: current.application,
      applicationPlanningInteraction: interaction,
      editorMode: 'frontend',
      originalRequest: buildApplicationPlanningRequest(current.application),
      workflowAction: designRevision
        ? designRevision.impact.formalBranch === 'workbench_plan_revision'
          ? 'start_technical_revision'
          : 'start_design_revision'
        : workflowAction,
      revisionRequest: designRevision
        ? {
            source: 'conversation_handoff',
            formalBranch: designRevision.impact.formalBranch,
            target: designRevision.target,
            request: designRevision.request,
            confirmedImpact: { interactionId: designRevision.impact.interactionId }
          }
        : undefined,
      workflowDebug:
        interaction || designRevision || workflowAction
          ? undefined
          : workflowDebugOverride || {
              enabled: true,
              resumeFrom: planningResumeFrom(current.lifecycle)
            },
      workflowScope: 'application_planning',
      workspaceRoot: current.application.workspaceRoot
    }
  }

  /** 独占一次逻辑 transport ownership，并保留停止失败后下一轮必须重新 stop 的语义。 */
  private async withExclusiveTransport<T>(
    operation: (token: number) => Promise<T>,
    options: { stopPrevious?: boolean; onToken?: (token: number) => void } = {}
  ): Promise<T> {
    const previousRunActive = this.runActive || this.session.hasActiveRun()
    if (previousRunActive && !options.stopPrevious) {
      throw new Error('当前规划仍在执行，请稍后重试。')
    }
    const token = ++this.runToken
    options.onToken?.(token)
    this.runActive = true
    this.dispatch({
      type: 'run_started',
      applicationId: this.applicationId,
      threadId: this.threadId
    })
    this.setStreamingContent('')
    let previousRunStopFailed = false
    try {
      if (previousRunActive) {
        try {
          await this.session.stop()
        } catch (reason) {
          previousRunStopFailed = true
          throw reason
        }
      }
      if (!this.isCurrentRun(token)) throw new Error('当前 Planning Runtime 已失效。')
      return await operation(token)
    } finally {
      if (this.isCurrentRun(token)) {
        // 旧 run 停止未获确认时保留活动标记，下一次操作必须先重新 stop。
        this.runActive = previousRunStopFailed
        this.dispatch({
          type: 'run_settled',
          applicationId: this.applicationId,
          threadId: this.threadId
        })
      }
    }
  }

  /** 作为 Runtime 唯一 AG-UI 发送点，保证 workflow 先写 Canonical 再发布历史。 */
  private async sendMessageWithinTransport(
    token: number,
    messageText: string,
    options: SendWorkflowMessageOptions,
    session: Pick<AgUiChatSession, 'sendMessage'> = this.session
  ): Promise<WorkflowRunPayload | undefined> {
    if (!this.isCurrentRun(token)) throw new Error('当前 Planning Runtime 已失效。')
    const result = await session.sendMessage(messageText, {
      ...options,
      onContent: (content) => {
        if (!this.isCurrentRun(token)) return
        this.setStreamingContent(content)
        this.dependencies.publishContent(content)
      },
      // Canonical 写入必须先于可发布判断，终态帧也不能等 RunFinished 才可见。
      onWorkflow: (workflow) => {
        if (!this.isCurrentRun(token)) return
        const merged = this.applyWorkflow(workflow)
        if (merged && planningWorkflowCanPublishDuringRun(workflow))
          this.dependencies.publishWorkflow(merged)
      }
    })
    if (!this.isCurrentRun(token)) return undefined
    const merged = result.workflow ? this.applyWorkflow(result.workflow) : undefined
    if (merged) this.dependencies.publishWorkflow(merged)
    return merged
  }

  /** 处理一次 Planning 响应的交接和技术规划确认，transport 生命周期由外层统一管理。 */
  private async handlePlanningResult(
    token: number,
    workflow: WorkflowRunPayload | undefined
  ): Promise<void> {
    const handoff = revisionContinuationHandoffFromWorkflow(workflow)
    if (handoff) {
      this.completed = true
      await this.dependencies.onRevisionContinuation(handoff)
      return
    }
    const confirmation = workflowConfirmation(workflow)
    if (confirmation && !this.completed) {
      this.completed = true
      try {
        const succeeded = await this.dependencies.onTechnicalPlanConfirmed(confirmation)
        if (!this.isCurrentRun(token) || succeeded) return
        this.completed = false
        this.dispatch({
          type: 'template_generation_failed',
          applicationId: this.applicationId,
          threadId: this.threadId,
          error: this.requireCurrentState().error || '应用模板准备失败，模板生成已终止。'
        })
      } catch (reason) {
        this.completed = false
        throw reason
      }
    }
  }
}
