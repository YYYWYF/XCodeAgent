import type {
  ApplicationPlanningConfirmation,
  ApplicationPlanningInteraction,
  WorkflowClarificationAnswers,
  WorkflowDesignStageRevisionStart,
  WorkflowRunPayload
} from '../typings'
import {
  AgUiChatSession,
  AgUiRunError,
  getExecutionRecoveryUrl,
  type SendWorkflowMessageOptions
} from './agUiAgent'
import {
  applicationPlanningDisplayStatus,
  planningMutationBlocked,
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
import {
  planningWorkflowCanPublishDuringRun,
  planningWorkflowRequiresUserInput
} from './applicationPlanningWorkflowState'
import {
  ApplicationPlanningCheckpointNotFoundError,
  readApplicationPlanningAuthoritativeSnapshot,
  type ApplicationPlanningAuthoritativeSnapshot,
  type ApplicationPlanningRecoveryProjection
} from './applicationPlanningRecovery'
import {
  buildPlanningInteraction, hasPlanningInterrupt,
  MISSING_INTERRUPT_ERROR, planningInterruptIdentity, planningResumeFrom, planningRuntimeError,
  waitForStoppedPlanningLifecycle, withAuthoritativeLifecycle, withSavedRequirementSpec,
  workflowConfirmation
} from './applicationPlanningRuntimeHelpers'
import { productConversationSubmissionError } from './applicationPlanningProductConversation'

export type ApplicationPlanningRuntimeDependencies = {
  applicationId: string
  threadId: string
  getCurrentState: () => ApplicationPlanningCurrentState | undefined
  dispatchCurrentEvent: (event: ApplicationPlanningCurrentEvent) => void
  publishContent: (content: string) => void
  publishWorkflow: (workflow: WorkflowRunPayload) => void
  onTechnicalPlanConfirmed: (confirmation: ApplicationPlanningConfirmation) => Promise<boolean>
  onRevisionContinuation: (handoff: WorkflowRevisionContinuationHandoff) => Promise<void>
  session?: Pick<AgUiChatSession, 'sendMessage' | 'stop' | 'hasActiveRun'>
  /** 测试可替换短生命周期 Native Recovery 会话；生产不复用普通 Planning endpoint。 */
  createRecoverySession?: (
    threadId: string
  ) => Pick<AgUiChatSession, 'sendMessage'>
  /** 测试可替换保存服务；生产仍使用原有 AG-UI 保存入口。 */
  saveRequirementSpecDraft?: typeof saveRequirementSpecDraft
  /** 测试可替换权威读取；生产使用独立 applicationPlanningRecovery client。 */
  readAuthoritativeSnapshot?: (
    application: ApplicationPlanningCurrentState['application'],
    threadId: string
  ) => Promise<ApplicationPlanningAuthoritativeSnapshot>
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
  private readonly session: NonNullable<ApplicationPlanningRuntimeDependencies['session']>
  private started = false
  private completed = false
  private disposed = false
  private runToken = 0
  private runActive = false
  private reconcilePromise?: Promise<PlanningReconcileOutcome>
  private streamingContent = ''
  private streamingListeners = new Set<(content: string) => void>()

  /** 绑定应用和线程身份，只保存会话执行能力，不捕获业务快照。 */
  constructor(private readonly dependencies: ApplicationPlanningRuntimeDependencies) {
    this.applicationId = dependencies.applicationId
    this.threadId = dependencies.threadId
    this.session = dependencies.session ?? createApplicationPlanningSession(this.threadId)
  }

  /** 在每次动作时检查当前身份，已删除或换线程的 Runtime 不得继续执行。 */
  private requireCurrentState(): ApplicationPlanningCurrentState {
    const state = this.dependencies.getCurrentState()
    if (this.disposed || !state || state.application.id !== this.applicationId || state.threadId !== this.threadId) {
      throw new Error('当前 Planning Runtime 已失效。')
    }
    return state
  }

  /** 静默丢弃过期运行和已失效实例的异步回调。 */
  private isCurrentRun(token: number): boolean {
    const current = this.dependencies.getCurrentState()
    return !this.disposed && token === this.runToken && current?.application.id === this.applicationId && current.threadId === this.threadId
  }

  /** 通过唯一事件入口提交当前实例的状态变化。 */
  private dispatch(event: ApplicationPlanningCurrentEvent): void {
    this.requireCurrentState()
    this.dependencies.dispatchCurrentEvent(event)
  }

  /** 阻止未同步状态继续发起可能重复消费用户动作的 Graph 写请求。 */
  private assertMutationAllowed(): void {
    if (planningMutationBlocked(this.requireCurrentState())) {
      throw new Error('当前规划状态尚未与后端完成同步，请先重新同步状态。')
    }
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
    this.dispatch({ type: 'workflow_received', applicationId: this.applicationId, threadId: this.threadId, workflow })
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
    return () => { this.streamingListeners.delete(listener) }
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
      if (planningWorkflowRequiresUserInput(current.workflow)) return
    }
    if (['generating_application_template_files', 'application_template_generation_failed', 'ready_for_workbench'].includes(current.lifecycle.initialization.stage)) return
    if (current.lifecycle.initialization.status === 'awaiting_user') {
      await this.reconcileCurrentState()
      return
    }
    if (applicationPlanningDisplayStatus(current) !== 'running') return
    await this.runPlanning(current.workflow ? '请从上次保存的规划状态继续执行。' : buildApplicationPlanningRequest(current.application))
  }

  /** 先对账再由 Backend Recovery Projection 决定是否创建 Native Recovery child。 */
  async retryCurrentFailure(): Promise<void> {
    this.assertMutationAllowed()
    const outcome = await this.reconcileCurrentState()
    if (outcome.status !== 'recovered') return
    const current = this.requireCurrentState()
    const recovery = current.recovery
    if (!recovery || recovery.classification === 'awaiting_user') return
    if (
      recovery.classification !== 'ready_to_continue' ||
      !recovery.canContinue ||
      !recovery.sourceRunId
    ) {
      return
    }
    await this.continueInterruptedPlanning(recovery)
  }

  /** 正式设计修订直接使用原 Planning 会话发送用户请求。 */
  async startDesignRevision(input: WorkflowDesignStageRevisionStart): Promise<void> {
    this.assertMutationAllowed()
    await this.runPlanning(input.request, undefined, input)
  }

  /** 确认操作优先使用当前快照，只把调用方卡片用于线程有效性检查。 */
  async submitClarification(
    workflow: WorkflowRunPayload,
    answers: WorkflowClarificationAnswers,
    editedRequirementSpec?: Record<string, unknown>,
    requirementSpecFeedback?: string,
    designChangeRequest?: string
  ): Promise<void> {
    let current = this.requireCurrentState()
    this.assertMutationAllowed()
    if (workflow.threadId !== current.threadId) throw new Error('当前规划确认卡已经失效。')
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
      await this.withExclusiveTransport(async (token) => {
        const interaction = buildPlanningInteraction(
          submittable, answers, editedRequirementSpec, requirementSpecFeedback, designChangeRequest
        )
        const latest = this.requireCurrentState()
        const merged = await this.sendMessageWithinTransport(
          token,
          designChangeRequest?.trim() || '请根据本轮确认继续创建规划。',
          this.planningRunOptions(latest, interaction)
        )
        await this.handlePlanningResult(token, merged)
      }, { stopPrevious: true, onToken: (token) => { submitToken = token } })
    } catch (reason) {
      if (submitToken === undefined || !this.isCurrentRun(submitToken)) throw reason
      const outcome = await this.handleExecutionFailure(
        reason,
        designChangeRequest ? productConversationSubmissionError(reason) : planningRuntimeError(reason, '创建规划确认失败')
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
  async saveRequirementSpec(spec: Record<string, unknown>): Promise<Awaited<ReturnType<typeof saveRequirementSpecDraft>>> {
    this.assertMutationAllowed()
    const current = this.requireCurrentState()
    const save = this.dependencies.saveRequirementSpecDraft ?? saveRequirementSpecDraft
    const saved = await save(current.application.workspaceRoot || '', spec, current.threadId)
    const latest = this.requireCurrentState().workflow
    if (latest) this.applyWorkflow(withSavedRequirementSpec(latest, saved))
    return saved
  }

  /** 停止 transport 后等待服务端取消落盘，并刷新 Canonical 生命周期。 */
  async stop(): Promise<void> {
    this.requireCurrentState()
    try {
      await this.session.stop()
      const lifecycle = await waitForStoppedPlanningLifecycle(() => this.requireCurrentState().application, this.threadId)
      this.dispatch({ type: 'lifecycle_received', applicationId: this.applicationId, threadId: this.threadId, lifecycle })
      const workflow = this.requireCurrentState().workflow
      if (workflow) this.applyWorkflow(withAuthoritativeLifecycle(workflow, lifecycle))
    } catch (reason) {
      if (isAuthenticationFailure(reason)) throw reason
      // stop 结果不确定时让旧回调失效，由共享后端 barrier 等待真实 writer 收口。
      this.runActive = false
      const outcome = await this.reconcileAfterTransportFailure(reason)
      if (outcome !== 'recovered') throw reason
    }
  }

  /** 以 single-flight 方式读取并原子提交当前 application planning 权威快照。 */
  async reconcileCurrentState(): Promise<PlanningReconcileOutcome> {
    if (this.reconcilePromise) return this.reconcilePromise
    this.reconcilePromise = this.performReconcile()
    try {
      return await this.reconcilePromise
    } finally {
      this.reconcilePromise = undefined
    }
  }

  /** 执行一次只读对账，任何失败都只进入 syncError，不伪造业务失败。 */
  private async performReconcile(): Promise<PlanningReconcileOutcome> {
    const current = this.requireCurrentState()
    if (this.runActive) throw new Error('当前 Planning write transport 尚未结束。')
    const token = ++this.runToken
    this.dispatch({ type: 'reconcile_started', applicationId: this.applicationId, threadId: this.threadId })
    try {
      const read = this.dependencies.readAuthoritativeSnapshot ?? readApplicationPlanningAuthoritativeSnapshot
      const snapshot = await read(current.application, this.threadId)
      if (!this.isCurrentRun(token)) throw new Error('当前 Planning Runtime 已失效。')
      if (
        snapshot.workflow.threadId !== this.threadId ||
        snapshot.lifecycle.application.id !== this.applicationId
      ) {
        throw new Error('应用规划权威快照身份不匹配。')
      }
      this.dispatch({
        type: 'reconcile_received', applicationId: this.applicationId, threadId: this.threadId,
        lifecycle: snapshot.lifecycle, workflow: snapshot.workflow, recovery: snapshot.recovery
      })
      const workflow = this.requireCurrentState().workflow
      if (workflow) this.dependencies.publishWorkflow(workflow)
      return { status: 'recovered' }
    } catch (reason) {
      if (!this.isCurrentRun(token)) {
        return { status: 'uncertain', error: '当前 Planning Runtime 已失效。' }
      }
      if (reason instanceof ApplicationPlanningCheckpointNotFoundError) {
        if (this.isFreshInitialState(current)) {
          this.dispatch({ type: 'run_settled', applicationId: this.applicationId, threadId: this.threadId })
        } else {
          this.dispatch({
            type: 'reconcile_failed', applicationId: this.applicationId, threadId: this.threadId,
            error: reason.message
          })
        }
        return { status: 'checkpoint_missing' }
      }
      const error = planningRuntimeError(reason, PLANNING_SYNC_ERROR)
      this.dispatch({ type: 'reconcile_failed', applicationId: this.applicationId, threadId: this.threadId, error })
      return { status: 'uncertain', error }
    }
  }

  /** 把普通 network/SSE/finalization 异常转为一次只读权威对账。 */
  private async reconcileAfterTransportFailure(_reason: unknown): Promise<PlanningExecutionFailure> {
    const outcome = await this.reconcileCurrentState()
    if (outcome.status === 'recovered') return 'recovered'
    if (outcome.status === 'checkpoint_missing') {
      this.dispatch({
        type: 'reconcile_failed', applicationId: this.applicationId, threadId: this.threadId,
        error: PLANNING_SYNC_ERROR
      })
    }
    return 'uncertain'
  }

  /** 使用独立 `/execution-recovery/run` 会话继续同一 Planning thread，不重发用户答案。 */
  private async continueInterruptedPlanning(
    recovery: ApplicationPlanningRecoveryProjection
  ): Promise<void> {
    let recoveryToken: number | undefined
    try {
      await this.withExclusiveTransport(
        async (token) => {
          const current = this.requireCurrentState()
          const session = this.dependencies.createRecoverySession
            ? this.dependencies.createRecoverySession(recovery.threadId)
            : new AgUiChatSession(recovery.threadId, getExecutionRecoveryUrl())
          const merged = await this.sendMessageWithinTransport(
            token,
            '继续执行上一次中断的规划。',
            {
              editorMode: 'frontend',
              workspaceRoot: current.application.workspaceRoot,
              executionRecovery: {
                action: 'continue',
                sourceRunId: recovery.sourceRunId!
              }
            },
            session
          )
          await this.handlePlanningResult(token, merged)
        },
        { onToken: (token) => { recoveryToken = token } }
      )
    } catch (reason) {
      if (recoveryToken === undefined || !this.isCurrentRun(recoveryToken)) return
      await this.handleExecutionFailure(
        reason,
        planningRuntimeError(reason, '继续执行中断的规划失败')
      )
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
        type: 'run_failed', applicationId: this.applicationId, threadId: this.threadId,
        error: reason.message || fallback, workflow
      })
      return 'authoritative_failure'
    }
    // stopPrevious 或主 SSE 失败后旧 token 必须立即失效；后端共享 barrier 负责等待 writer。
    this.runActive = false
    return this.reconcileAfterTransportFailure(reason)
  }

  /** 使晚到帧立即失效并释放会话与正文订阅，停止失败不会写回已移除的应用。 */
  dispose(): void {
    if (this.disposed) return
    this.disposed = true
    this.runToken += 1
    this.runActive = false
    this.streamingListeners.clear()
    this.streamingContent = ''
    void this.session.stop().catch((reason: unknown) => { console.error('[planning-runtime] dispose stop failed', reason) })
  }

  /** 初始生成和卡片恢复共用同一个带代次保护的运行入口。 */
  private async runPlanning(
    messageText: string,
    interaction?: ApplicationPlanningInteraction,
    designRevision?: WorkflowDesignStageRevisionStart
  ): Promise<void> {
    const current = this.requireCurrentState()
    if (!current.application.workspaceRoot) return
    const previousRunActive = this.runActive || this.session.hasActiveRun()
    if (previousRunActive && !interaction && !designRevision) return
    if (previousRunActive && interaction?.action === 'design_change') throw new Error('当前设计正在生成，完成后即可发送新的调整。')
    let token: number | undefined
    try {
      await this.withExclusiveTransport(async (currentToken) => {
        const latest = this.requireCurrentState()
        const merged = await this.sendMessageWithinTransport(
          currentToken,
          messageText,
          this.planningRunOptions(latest, interaction, designRevision)
        )
        await this.handlePlanningResult(currentToken, merged)
      }, {
        stopPrevious: Boolean(interaction || designRevision),
        onToken: (currentToken) => { token = currentToken }
      })
    } catch (reason) {
      if (token === undefined || !this.isCurrentRun(token)) {
        if (interaction || designRevision) throw reason
        return
      }
      const outcome = await this.handleExecutionFailure(
        reason,
        planningRuntimeError(reason, '创建规划运行失败')
      )
      if ((interaction || designRevision) && outcome !== 'recovered') throw reason
    }
  }

  /** 构造普通 Planning 请求选项，始终从调用瞬间的 Canonical State 读取业务事实。 */
  private planningRunOptions(
    current: ApplicationPlanningCurrentState,
    interaction?: ApplicationPlanningInteraction,
    designRevision?: WorkflowDesignStageRevisionStart
  ): SendWorkflowMessageOptions {
    return {
      application: current.application, applicationPlanningInteraction: interaction, editorMode: 'frontend',
      originalRequest: buildApplicationPlanningRequest(current.application),
      workflowAction: designRevision ? 'start_design_revision' : undefined,
      revisionRequest: designRevision ? {
        source: 'conversation_handoff', formalBranch: designRevision.impact.formalBranch,
        target: designRevision.target, request: designRevision.request,
        confirmedImpact: { interactionId: designRevision.impact.interactionId }
      } : undefined,
      workflowDebug: interaction || designRevision ? undefined : { enabled: true, resumeFrom: planningResumeFrom(current.lifecycle) },
      workflowScope: 'application_planning', workspaceRoot: current.application.workspaceRoot
    }
  }

  /** 独占一次逻辑 transport ownership，并保留停止失败后下一轮必须重新 stop 的语义。 */
  private async withExclusiveTransport<T>(
    operation: (token: number) => Promise<T>,
    options: { stopPrevious?: boolean; onToken?: (token: number) => void } = {}
  ): Promise<T> {
    this.assertMutationAllowed()
    const previousRunActive = this.runActive || this.session.hasActiveRun()
    if (previousRunActive && !options.stopPrevious) {
      throw new Error('当前规划仍在执行，请稍后重试。')
    }
    const token = ++this.runToken
    options.onToken?.(token)
    this.runActive = true
    this.dispatch({ type: 'run_started', applicationId: this.applicationId, threadId: this.threadId })
    this.setStreamingContent('')
    let previousRunStopFailed = false
    try {
      if (previousRunActive) {
        try { await this.session.stop() } catch (reason) { previousRunStopFailed = true; throw reason }
      }
      if (!this.isCurrentRun(token)) throw new Error('当前 Planning Runtime 已失效。')
      return await operation(token)
    } finally {
      if (this.isCurrentRun(token)) {
        // 旧 run 停止未获确认时保留活动标记，下一次操作必须先重新 stop。
        this.runActive = previousRunStopFailed
        this.dispatch({ type: 'run_settled', applicationId: this.applicationId, threadId: this.threadId })
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
        if (merged && planningWorkflowCanPublishDuringRun(workflow)) this.dependencies.publishWorkflow(merged)
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
        this.dispatch({ type: 'run_failed', applicationId: this.applicationId, threadId: this.threadId, error: '应用模板准备失败，模板生成已终止。' })
      } catch (reason) {
        this.completed = false
        throw reason
      }
    }
  }
}
