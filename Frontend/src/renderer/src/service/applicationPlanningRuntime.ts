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
  buildPlanningInteraction, fetchRecoveryWorkflowWithRetry, hasPlanningInterrupt,
  MISSING_INTERRUPT_ERROR, planningRecoveryOptions, planningResumeFrom, planningRuntimeError,
  waitForStoppedPlanningLifecycle, withAuthoritativeLifecycle, withSavedRequirementSpec,
  workflowConfirmation
} from './applicationPlanningRuntimeHelpers'
import { productConversationSubmissionError } from '../components/AiChatPanel/components/ChatComposer/productConversation'

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
    const token = this.runToken
    let submitted = false
    try {
      if (Object.keys(answers).length === 0 && !designChangeRequest?.trim()) return await this.recover()
      await this.loadSubmittablePlanningWorkflow()
      const submittable = this.requireCurrentState().workflow
      if (!submittable) throw new Error('当前规划状态尚未就绪，请稍后重试。')
      if (token !== this.runToken) throw new Error('当前规划确认卡已经更新，请重新提交。')
      const interaction = buildPlanningInteraction(submittable, answers, editedRequirementSpec, requirementSpecFeedback, designChangeRequest)
      submitted = true
      await this.runPlanning(designChangeRequest?.trim() || '请根据本轮确认继续创建规划。', interaction)
    } catch (reason) {
      // runPlanning 自己处理运行失败；提交前构造失败也必须交回消息层回滚乐观提交。
      if (this.isCurrentRun(this.runToken) && (token === this.runToken || (submitted && token + 1 === this.runToken))) {
        this.dispatch({ type: 'run_failed', applicationId: this.applicationId, threadId: this.threadId,
          error: designChangeRequest ? productConversationSubmissionError(reason) : planningRuntimeError(reason, '创建规划确认失败') })
      }
      throw reason
    }
  }

  /** 必要时只读恢复中断；恢复后的最新 Canonical 快照仍拥有最终优先级。 */
  private async loadSubmittablePlanningWorkflow(): Promise<WorkflowRunPayload> {
    const current = this.requireCurrentState()
    if (current.workflow && hasPlanningInterrupt(current.workflow)) return current.workflow
    if (!current.application.workspaceRoot) throw new Error(MISSING_INTERRUPT_ERROR)
    const token = this.runToken
    const recovered = await fetchRecoveryWorkflowWithRetry(this.session, () => this.requireCurrentState().application)
    this.requireCurrentState()
    if (token !== this.runToken) throw new Error('当前规划确认卡已经更新，请重新提交。')
    const latest = this.requireCurrentState().workflow
    const submittable = latest && hasPlanningInterrupt(latest) ? latest : this.applyWorkflow(recovered)
    if (!submittable || !hasPlanningInterrupt(submittable)) throw new Error(MISSING_INTERRUPT_ERROR)
    return submittable
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
    await this.execute(messageText, false, previousRunActive, interaction, designRevision)
  }

  /** 冷启动恢复仅请求 checkpoint，状态描述不投递为产品 Agent 对话。 */
  private async recover(): Promise<void> {
    const current = this.requireCurrentState()
    if (!current.application.workspaceRoot || this.runActive || this.session.hasActiveRun()) return
    await this.execute('读取待确认的应用规划状态。', true, false)
  }

  /** 统一处理运行代次、AG-UI 帧顺序、交接及 transport 结束语义。 */
  private async execute(
    messageText: string, recovery: boolean, previousRunActive: boolean,
    interaction?: ApplicationPlanningInteraction, designRevision?: WorkflowDesignStageRevisionStart
  ): Promise<void> {
    const token = ++this.runToken
    this.runActive = true
    this.dispatch({ type: 'run_started', applicationId: this.applicationId, threadId: this.threadId })
    this.setStreamingContent('')
    let previousRunStopFailed = false
    try {
      if (previousRunActive) {
        try { await this.session.stop() } catch (reason) { previousRunStopFailed = true; throw reason }
      }
      if (!this.isCurrentRun(token)) return
      const current = this.requireCurrentState()
      const options: SendWorkflowMessageOptions = recovery ? planningRecoveryOptions(current.application) : {
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
      const result = await this.session.sendMessage(messageText, {
        ...options,
        // 恢复正文只用于可见视图，不混入产品 Agent 消息历史。
        onContent: (content) => {
          if (!this.isCurrentRun(token)) return
          this.setStreamingContent(content)
          if (!recovery) this.dependencies.publishContent(content)
        },
        // Canonical 写入必须先于可发布判断，终态帧也不能等 RunFinished 才可见。
        onWorkflow: (workflow) => {
          if (!this.isCurrentRun(token)) return
          const merged = this.applyWorkflow(workflow)
          if (merged && planningWorkflowCanPublishDuringRun(workflow)) this.dependencies.publishWorkflow(merged)
        }
      })
      if (!this.isCurrentRun(token)) return
      const merged = result.workflow ? this.applyWorkflow(result.workflow) : undefined
      if (merged) this.dependencies.publishWorkflow(merged)
      const handoff = revisionContinuationHandoffFromWorkflow(merged)
      if (handoff) {
        this.completed = true
        await this.dependencies.onRevisionContinuation(handoff)
        return
      }
      const confirmation = recovery ? undefined : workflowConfirmation(merged)
      if (confirmation && !this.completed) {
        this.completed = true
        try {
          const succeeded = await this.dependencies.onTechnicalPlanConfirmed(confirmation)
          if (!this.isCurrentRun(token) || succeeded) return
          this.completed = false
          this.dispatch({ type: 'run_failed', applicationId: this.applicationId, threadId: this.threadId, error: '应用模板准备失败，模板生成已终止。' })
        } catch (reason) { this.completed = false; throw reason }
      }
    } catch (reason) {
      if (!this.isCurrentRun(token)) return
      if (!isAuthenticationFailure(reason)) this.dispatch({ type: 'run_failed', applicationId: this.applicationId, threadId: this.threadId,
        error: planningRuntimeError(reason, recovery ? '恢复待确认规划失败' : '创建规划运行失败') })
      if (interaction || designRevision) throw reason
    } finally {
      if (this.isCurrentRun(token)) {
        // 旧 run 停止未获确认时保留活动标记，下一次操作必须先重新 stop。
        this.runActive = previousRunStopFailed
        this.dispatch({ type: 'run_settled', applicationId: this.applicationId, threadId: this.threadId })
      }
    }
  }
}
