import type {
  ApplicationPlanningConfirmation,
  ApplicationPlanningInteraction,
  WorkflowClarificationAnswers,
  WorkflowDesignStageRevisionStart,
  WorkflowRunPayload
} from '../typings'
import type { AgUiChatSession, SendWorkflowMessageOptions } from './agUiAgent'
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
import { planningWorkflowCanPublishDuringRun } from './applicationPlanningWorkflowState'
import {
  buildPlanningInteraction, hasPlanningInterrupt,
  MISSING_INTERRUPT_ERROR, planningRecoveryOptions, planningResumeFrom, planningRuntimeError,
  retryPlanningRecoveryRead,
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
  /** 测试可替换保存服务；生产仍使用原有 AG-UI 保存入口。 */
  saveRequirementSpecDraft?: typeof saveRequirementSpecDraft
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
    const current = this.requireCurrentState()
    if (this.started) return
    this.started = true
    if (['generating_application_template_files', 'application_template_generation_failed', 'ready_for_workbench'].includes(current.lifecycle.initialization.stage)) return
    if (current.lifecycle.initialization.status === 'awaiting_user') return this.recover()
    if (applicationPlanningDisplayStatus(current) !== 'running') return
    await this.runPlanning(current.workflow ? '请从上次保存的规划状态继续执行。' : buildApplicationPlanningRequest(current.application))
  }

  /** 重试只读取调用时的最新生命周期，并保留技术规划已确认的终止边界。 */
  async retryCurrentFailure(): Promise<void> {
    const current = this.requireCurrentState()
    if (workflowConfirmation(current.workflow)) return
    if (current.lifecycle.initialization.status === 'awaiting_user') return this.recover()
    await this.runPlanning(buildApplicationPlanningRequest(current.application))
  }

  /** 正式设计修订直接使用原 Planning 会话发送用户请求。 */
  async startDesignRevision(input: WorkflowDesignStageRevisionStart): Promise<void> {
    this.requireCurrentState()
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
    const current = this.requireCurrentState()
    if (!current.workflow) throw new Error('当前规划状态尚未就绪，请稍后重试。')
    if (workflow.threadId !== current.threadId) throw new Error('当前规划确认卡已经失效。')
    if ((this.runActive || this.session.hasActiveRun()) && designChangeRequest?.trim()) {
      throw new Error('当前设计正在生成，完成后即可发送新的调整。')
    }
    let submitToken: number | undefined
    try {
      if (Object.keys(answers).length === 0 && !designChangeRequest?.trim()) return await this.recover()
      await this.withExclusiveTransport(async (token) => {
        submitToken = token
        const submittable = await this.loadSubmittableWorkflowWithinTransport(token)
        const interaction = buildPlanningInteraction(
          submittable, answers, editedRequirementSpec, requirementSpecFeedback, designChangeRequest
        )
        const latest = this.requireCurrentState()
        const merged = await this.sendMessageWithinTransport(
          token,
          designChangeRequest?.trim() || '请根据本轮确认继续创建规划。',
          this.planningRunOptions(latest, interaction),
          'planning'
        )
        await this.handlePlanningResult(token, merged, false)
      }, { stopPrevious: true })
    } catch (reason) {
      // 提交失败必须交回消息层回滚乐观提交，过期运行不能覆盖新一轮状态。
      if (submitToken !== undefined && this.isCurrentRun(submitToken)) {
        this.dispatch({ type: 'run_failed', applicationId: this.applicationId, threadId: this.threadId,
          error: designChangeRequest ? productConversationSubmissionError(reason) : planningRuntimeError(reason, '创建规划确认失败') })
      }
      throw reason
    }
  }

  /** 在当前 ownership 内只读恢复中断；最新 Canonical 快照始终拥有最终优先级。 */
  private async loadSubmittableWorkflowWithinTransport(token: number): Promise<WorkflowRunPayload> {
    const current = this.requireCurrentState()
    if (current.workflow && hasPlanningInterrupt(current.workflow)) return current.workflow
    if (!current.application.workspaceRoot) throw new Error(MISSING_INTERRUPT_ERROR)
    const recovered = await retryPlanningRecoveryRead(async () => {
      const latest = this.requireCurrentState()
      const workflow = await this.sendMessageWithinTransport(
        token,
        '读取待确认的应用规划状态。',
        planningRecoveryOptions(latest.application),
        'recovery'
      )
      if (!workflow) throw new Error('恢复响应缺少规划快照')
      return workflow
    })
    if (!this.isCurrentRun(token)) throw new Error('当前规划确认卡已经更新，请重新提交。')
    const latest = this.requireCurrentState().workflow
    const candidate = latest && hasPlanningInterrupt(latest) ? latest : recovered
    if (!hasPlanningInterrupt(candidate)) throw new Error(MISSING_INTERRUPT_ERROR)
    return candidate
  }

  /** 保存需求草稿后更新当前快照，提示消息由调用它的 UI 决定。 */
  async saveRequirementSpec(spec: Record<string, unknown>): Promise<Awaited<ReturnType<typeof saveRequirementSpecDraft>>> {
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
    await this.session.stop()
    const lifecycle = await waitForStoppedPlanningLifecycle(() => this.requireCurrentState().application, this.threadId)
    this.dispatch({ type: 'lifecycle_received', applicationId: this.applicationId, threadId: this.threadId, lifecycle })
    const workflow = this.requireCurrentState().workflow
    if (workflow) this.applyWorkflow(withAuthoritativeLifecycle(workflow, lifecycle))
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
        token = currentToken
        const latest = this.requireCurrentState()
        const merged = await this.sendMessageWithinTransport(
          currentToken,
          messageText,
          this.planningRunOptions(latest, interaction, designRevision),
          'planning'
        )
        await this.handlePlanningResult(currentToken, merged, false)
      }, { stopPrevious: Boolean(interaction || designRevision) })
    } catch (reason) {
      if (token === undefined || !this.isCurrentRun(token)) {
        if (interaction || designRevision) throw reason
        return
      }
      if (!isAuthenticationFailure(reason)) {
        this.dispatch({ type: 'run_failed', applicationId: this.applicationId, threadId: this.threadId,
          error: planningRuntimeError(reason, '创建规划运行失败') })
      }
      if (interaction || designRevision) throw reason
    }
  }

  /** 冷启动恢复仅请求 checkpoint，状态描述不投递为产品 Agent 对话。 */
  private async recover(): Promise<void> {
    const current = this.requireCurrentState()
    if (!current.application.workspaceRoot || this.runActive || this.session.hasActiveRun()) return
    let token: number | undefined
    try {
      await this.withExclusiveTransport(async (currentToken) => {
        token = currentToken
        const latest = this.requireCurrentState()
        const merged = await this.sendMessageWithinTransport(
          currentToken,
          '读取待确认的应用规划状态。',
          planningRecoveryOptions(latest.application),
          'recovery'
        )
        await this.handlePlanningResult(currentToken, merged, true)
      })
    } catch (reason) {
      if (token !== undefined && this.isCurrentRun(token) && !isAuthenticationFailure(reason)) {
        this.dispatch({ type: 'run_failed', applicationId: this.applicationId, threadId: this.threadId,
          error: planningRuntimeError(reason, '恢复待确认规划失败') })
      }
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
    options: { stopPrevious?: boolean } = {}
  ): Promise<T> {
    this.requireCurrentState()
    const previousRunActive = this.runActive || this.session.hasActiveRun()
    if (previousRunActive && !options.stopPrevious) {
      throw new Error('当前规划仍在执行，请稍后重试。')
    }
    const token = ++this.runToken
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
    mode: 'planning' | 'recovery'
  ): Promise<WorkflowRunPayload | undefined> {
    if (!this.isCurrentRun(token)) throw new Error('当前 Planning Runtime 已失效。')
    const result = await this.session.sendMessage(messageText, {
      ...options,
      // 恢复正文只用于可见视图，不混入产品 Agent 消息历史。
      onContent: (content) => {
        if (!this.isCurrentRun(token)) return
        this.setStreamingContent(content)
        if (mode === 'planning') this.dependencies.publishContent(content)
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
    workflow: WorkflowRunPayload | undefined,
    recovery: boolean
  ): Promise<void> {
    const handoff = revisionContinuationHandoffFromWorkflow(workflow)
    if (handoff) {
      this.completed = true
      await this.dependencies.onRevisionContinuation(handoff)
      return
    }
    const confirmation = recovery ? undefined : workflowConfirmation(workflow)
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
