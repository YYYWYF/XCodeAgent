import { useCallback, useEffect, useMemo, useRef } from 'react'
import type { ApplicationPlanningCurrentEvent, ApplicationPlanningCurrentState } from '../service/activeApplicationPlanning'
import {
  ApplicationPlanningRuntime,
  type ApplicationPlanningRuntimeDependencies
} from '../service/applicationPlanningRuntime'
import type { WorkflowRevisionContinuationHandoff } from '../service/applicationPagePlanning'
import { handoffPlanningStage, type PlanningStageEntry, type PlanningStageTarget } from '../service/planningStageHandoff'
import type { TechnicalPlanningReply } from '../service/technicalPlanningConversation'
import type {
  ApplicationPlanningDebugStage,
  ApplicationPlanningConfirmation,
  WorkflowDebugOptions,
  WorkflowRunPayload
} from '../typings'

export type ApplicationPlanningRuntimesOptions = {
  activePlannings: ApplicationPlanningCurrentState[]
  getPlanningState: (applicationId: string) => ApplicationPlanningCurrentState | undefined
  dispatchPlanningEvent: (event: ApplicationPlanningCurrentEvent) => void
  publishContent: (applicationId: string, content: string) => void
  publishReply: (applicationId: string, reply: TechnicalPlanningReply) => void
  publishWorkflow: (applicationId: string, workflow: WorkflowRunPayload) => void
  onTechnicalPlanConfirmed: (applicationId: string, confirmation: ApplicationPlanningConfirmation) => Promise<boolean>
  onRevisionContinuation: (applicationId: string, handoff: WorkflowRevisionContinuationHandoff) => Promise<void>
  /** 允许管理器测试注入会话；生产默认由 Runtime 创建 AG-UI 会话。 */
  createSession?: (threadId: string) => NonNullable<ApplicationPlanningRuntimeDependencies['session']>
}

export type ApplicationPlanningRuntimesController = {
  ensureRuntime: (planning: ApplicationPlanningCurrentState) => ApplicationPlanningRuntime
  getRuntime: (applicationId: string) => ApplicationPlanningRuntime | undefined
  submitClarification: (applicationId: string, workflow: WorkflowRunPayload,
    answers: Parameters<ApplicationPlanningRuntime['submitClarification']>[1],
    editedRequirementSpec?: Record<string, unknown>, requirementSpecFeedback?: string,
    designChangeRequest?: string, technicalPlanningConversation?: boolean, stageEntry?: PlanningStageEntry,
    conversationTarget?: import('../typings').PlanningConversationTarget) => Promise<void>
  saveRequirementSpec: (applicationId: string, ...args: Parameters<ApplicationPlanningRuntime['saveRequirementSpec']>) => ReturnType<ApplicationPlanningRuntime['saveRequirementSpec']>
  startDesignRevision: (applicationId: string, input: Parameters<ApplicationPlanningRuntime['startDesignRevision']>[0], target?: PlanningStageTarget) => Promise<void>
  retryCurrentFailure: (applicationId: string, restartInitialPlanning?: boolean) => Promise<void>
  startIterationPlanning: (
    applicationId: string,
    request: string,
    workflowDebug?: WorkflowDebugOptions,
    debugStage?: ApplicationPlanningDebugStage
  ) => Promise<void>
  retryTemplateReconcile: (applicationId: string) => Promise<void>
  reconcileCurrentState: (applicationId: string) => ReturnType<ApplicationPlanningRuntime['reconcileCurrentState']>
  stop: (applicationId: string) => Promise<void>
  subscribeStreamingContent: (applicationId: string, listener: (content: string) => void) => () => void
}

/** 应用和线程共同标识会话，换线程时绝不复用旧 Runtime。 */
export function planningRuntimeKey(applicationId: string, threadId: string): string {
  return `${applicationId}:${threadId}`
}

/** 在应用根部管理 Planning 执行器的生命周期，使后台运行独立于 Modal 挂载。 */
export function useApplicationPlanningRuntimes(
  options: ApplicationPlanningRuntimesOptions
): ApplicationPlanningRuntimesController {
  const runtimesRef = useRef(new Map<string, ApplicationPlanningRuntime>())
  const optionsRef = useRef(options)
  optionsRef.current = options

  /** 同步创建指定当前身份的执行器，并立即淘汰该应用旧线程实例。 */
  const ensureRuntime = useCallback((planning: ApplicationPlanningCurrentState): ApplicationPlanningRuntime => {
    const applicationId = planning.application.id
    const current = optionsRef.current.getPlanningState(applicationId)
    if (!current || current.threadId !== planning.threadId) throw new Error('当前 Planning Runtime 已失效。')
    const key = planningRuntimeKey(applicationId, current.threadId)
    for (const [existingKey, runtime] of runtimesRef.current) {
      if (runtime.applicationId === applicationId && existingKey !== key) {
        runtime.dispose()
        runtimesRef.current.delete(existingKey)
      }
    }
    const existing = runtimesRef.current.get(key)
    if (existing) return existing
    const runtime = new ApplicationPlanningRuntime({
      applicationId,
      threadId: current.threadId,
      getCurrentState: () => optionsRef.current.getPlanningState(applicationId),
      dispatchCurrentEvent: (event) => optionsRef.current.dispatchPlanningEvent(event),
      publishContent: (content) => optionsRef.current.publishContent(applicationId, content),
      publishReply: (reply) => optionsRef.current.publishReply(applicationId, reply),
      publishWorkflow: (workflow) => optionsRef.current.publishWorkflow(applicationId, workflow),
      onTechnicalPlanConfirmed: (confirmation) => optionsRef.current.onTechnicalPlanConfirmed(applicationId, confirmation),
      onRevisionContinuation: (handoff) => optionsRef.current.onRevisionContinuation(applicationId, handoff),
      session: optionsRef.current.createSession?.(current.threadId)
    })
    runtimesRef.current.set(key, runtime)
    return runtime
  }, [])

  /** 只返回 Canonical State 当前线程对应的现有 Runtime。 */
  const getRuntime = useCallback((applicationId: string): ApplicationPlanningRuntime | undefined => {
    const current = optionsRef.current.getPlanningState(applicationId)
    return current ? runtimesRef.current.get(planningRuntimeKey(applicationId, current.threadId)) : undefined
  }, [])

  /** 允许 startPlanning 同步返回后立即操作，无需等待 React effect 或 Modal。 */
  const requireRuntime = useCallback((applicationId: string): ApplicationPlanningRuntime => {
    const current = optionsRef.current.getPlanningState(applicationId)
    if (!current) throw new Error('当前 Planning Runtime 已失效。')
    return ensureRuntime(current)
  }, [ensureRuntime])

  /** 服务端交接成功后原子替换当前线程，再创建目标 Runtime；旧帧被身份检查丢弃。 */
  const handoffStage = useCallback(async (applicationId: string, target: PlanningStageTarget): Promise<WorkflowRunPayload> => {
    const current = optionsRef.current.getPlanningState(applicationId)
    if (!current) throw new Error('当前规划状态已失效。')
    if (current.threadId === target.threadId && current.workflow) return current.workflow
    const workflow = await handoffPlanningStage(current, target)
    optionsRef.current.dispatchPlanningEvent({ type: 'stage_thread_handed_off', applicationId,
      threadId: current.threadId, workflow })
    const next = optionsRef.current.getPlanningState(applicationId)
    if (next?.threadId !== target.threadId) throw new Error('阶段交接状态已被其他请求替换，请同步后重试。')
    ensureRuntime(next).acceptStageHandoff()
    return workflow
  }, [ensureRuntime])

  // 同步前端对象的存在性；脱离本地执行流的 running 由下方只读校准跟进。
  useEffect(() => {
    for (const [key, runtime] of runtimesRef.current) {
      const current = optionsRef.current.getPlanningState(runtime.applicationId)
      if (!current || current.threadId !== runtime.threadId) {
        runtime.dispose()
        runtimesRef.current.delete(key)
      }
    }
    for (const planning of options.activePlannings) {
      const current = optionsRef.current.getPlanningState(planning.application.id)
      if (!current || current.threadId !== planning.threadId) continue
      const runtime = ensureRuntime(current)
      void runtime.ensureStarted().catch((reason: unknown) => {
        console.error('[planning-runtime] ensureStarted failed', reason)
      })
    }
  }, [options.activePlannings, ensureRuntime])

  // 跟进脱离本地流的运行与 UI 后台生成池；只读 GET 不恢复 Graph，也不提交确认。
  useEffect(() => {
    const timer = window.setInterval(() => {
      for (const runtime of runtimesRef.current.values()) {
        void runtime.refreshDetachedRunningState().catch((reason: unknown) => {
          console.error('[planning-runtime] detached running reconcile failed', reason)
        })
      }
    }, 5000)
    return () => window.clearInterval(timer)
  }, [])

  // 应用根部真正卸载时才释放所有后台执行器；视图显隐不会触发这里。
  useEffect(() => () => {
    for (const runtime of runtimesRef.current.values()) runtime.dispose()
    runtimesRef.current.clear()
  }, [])

  // 网络恢复事件只为连接已失效的 Runtime 触发一次只读同步，不建立周期性轮询。
  useEffect(() => {
    const handleOnline = (): void => {
      for (const runtime of runtimesRef.current.values()) {
        const current = optionsRef.current.getPlanningState(runtime.applicationId)
        if (!current || current.threadId !== runtime.threadId || current.connection.status !== 'unavailable') {
          continue
        }
        void runtime.reconcileCurrentState().catch((reason: unknown) => {
          console.error('[planning-runtime] online reconcile failed', reason)
        })
      }
    }
    window.addEventListener('online', handleOnline)
    return () => window.removeEventListener('online', handleOnline)
  }, [])

  return useMemo<ApplicationPlanningRuntimesController>(() => ({
    ensureRuntime,
    getRuntime,
    /** 转交当前确认卡操作。 */
    submitClarification: async (applicationId, workflow, answers, edited, feedback, request, technical, stageEntry, conversationTarget) => {
      if (typeof stageEntry === 'function' || (technical && stageEntry)) {
        await requireRuntime(applicationId).submitClarification(workflow, answers, edited, feedback, request, technical,
          async () => {
            const target = typeof stageEntry === 'function' ? await stageEntry() : stageEntry!
            const transferred = await handoffStage(applicationId, target)
            await requireRuntime(applicationId).submitClarification(transferred, {}, undefined, undefined, request, false, undefined, conversationTarget)
          }, conversationTarget)
        return
      }
      const currentWorkflow = stageEntry ? await handoffStage(applicationId, stageEntry) : workflow
      await requireRuntime(applicationId).submitClarification(currentWorkflow, answers, edited, feedback, request, technical, undefined, conversationTarget)
    },
    /** 转交需求草稿保存。 */
    saveRequirementSpec: (applicationId, ...args) => requireRuntime(applicationId).saveRequirementSpec(...args),
    /** 转交正式设计修订。 */
    startDesignRevision: async (applicationId, input, target) => {
      if (target) {
        await requireRuntime(applicationId).reconcileCurrentState()
        await handoffStage(applicationId, target)
      }
      await requireRuntime(applicationId).startDesignRevision(input)
    },
    /** 使用调用时的当前状态重试。 */
    retryCurrentFailure: (applicationId, restartInitialPlanning) =>
      requireRuntime(applicationId).retryCurrentFailure(restartInitialPlanning),
    /** 新迭代发起后用户输入需求，启动 planning workflow。 */
    startIterationPlanning: (applicationId, request, workflowDebug, debugStage) =>
      requireRuntime(applicationId).startIterationPlanning(request, workflowDebug, debugStage),
    /** 使用专用 AG-UI 动作重试 Template Reconcile。 */
    retryTemplateReconcile: (applicationId) => requireRuntime(applicationId).retryTemplateReconcile(),
    /** 只读同步指定应用当前权威状态。 */
    reconcileCurrentState: (applicationId) => requireRuntime(applicationId).reconcileCurrentState(),
    /** 停止即结束指定应用的当前规划任务并释放占用。 */
    stop: (applicationId) => requireRuntime(applicationId).stop(),
    /** 订阅指定应用临时正文。 */
    subscribeStreamingContent: (applicationId, listener) => requireRuntime(applicationId).subscribeStreamingContent(listener)
  }), [ensureRuntime, getRuntime, requireRuntime, handoffStage])
}
