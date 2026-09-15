import { useCallback, useEffect, useMemo, useRef } from 'react'
import type { ApplicationPlanningCurrentEvent, ApplicationPlanningCurrentState } from '../service/activeApplicationPlanning'
import {
  ApplicationPlanningRuntime,
  type ApplicationPlanningRuntimeDependencies
} from '../service/applicationPlanningRuntime'
import type { WorkflowRevisionContinuationHandoff } from '../service/applicationPagePlanning'
import type { ApplicationPlanningConfirmation, WorkflowRunPayload } from '../typings'

export type ApplicationPlanningRuntimesOptions = {
  activePlannings: ApplicationPlanningCurrentState[]
  getPlanningState: (applicationId: string) => ApplicationPlanningCurrentState | undefined
  dispatchPlanningEvent: (event: ApplicationPlanningCurrentEvent) => void
  publishContent: (applicationId: string, content: string) => void
  publishWorkflow: (applicationId: string, workflow: WorkflowRunPayload) => void
  onTechnicalPlanConfirmed: (applicationId: string, confirmation: ApplicationPlanningConfirmation) => Promise<boolean>
  onRevisionContinuation: (applicationId: string, handoff: WorkflowRevisionContinuationHandoff) => Promise<void>
  /** 允许管理器测试注入会话；生产默认由 Runtime 创建 AG-UI 会话。 */
  createSession?: (threadId: string) => NonNullable<ApplicationPlanningRuntimeDependencies['session']>
}

export type ApplicationPlanningRuntimesController = {
  ensureRuntime: (planning: ApplicationPlanningCurrentState) => ApplicationPlanningRuntime
  getRuntime: (applicationId: string) => ApplicationPlanningRuntime | undefined
  submitClarification: (applicationId: string, ...args: Parameters<ApplicationPlanningRuntime['submitClarification']>) => Promise<void>
  saveRequirementSpec: (applicationId: string, ...args: Parameters<ApplicationPlanningRuntime['saveRequirementSpec']>) => ReturnType<ApplicationPlanningRuntime['saveRequirementSpec']>
  startDesignRevision: (applicationId: string, ...args: Parameters<ApplicationPlanningRuntime['startDesignRevision']>) => Promise<void>
  retryCurrentFailure: (applicationId: string) => Promise<void>
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

  // 这里只同步前端对象的存在性，不执行后台 checkpoint 对账或周期性轮询。
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

  // 应用根部真正卸载时才释放所有后台执行器；视图显隐不会触发这里。
  useEffect(() => () => {
    for (const runtime of runtimesRef.current.values()) runtime.dispose()
    runtimesRef.current.clear()
  }, [])

  // 网络恢复事件只为 uncertain Runtime 触发一次只读同步，不建立周期性轮询。
  useEffect(() => {
    const handleOnline = (): void => {
      for (const runtime of runtimesRef.current.values()) {
        const current = optionsRef.current.getPlanningState(runtime.applicationId)
        if (current?.threadId !== runtime.threadId || current.transportState !== 'uncertain') continue
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
    submitClarification: (applicationId, ...args) => requireRuntime(applicationId).submitClarification(...args),
    /** 转交需求草稿保存。 */
    saveRequirementSpec: (applicationId, ...args) => requireRuntime(applicationId).saveRequirementSpec(...args),
    /** 转交正式设计修订。 */
    startDesignRevision: (applicationId, ...args) => requireRuntime(applicationId).startDesignRevision(...args),
    /** 使用调用时的当前状态重试。 */
    retryCurrentFailure: (applicationId) => requireRuntime(applicationId).retryCurrentFailure(),
    /** 只读同步指定应用当前权威状态。 */
    reconcileCurrentState: (applicationId) => requireRuntime(applicationId).reconcileCurrentState(),
    /** 停止指定应用的会话。 */
    stop: (applicationId) => requireRuntime(applicationId).stop(),
    /** 订阅指定应用临时正文。 */
    subscribeStreamingContent: (applicationId, listener) => requireRuntime(applicationId).subscribeStreamingContent(listener)
  }), [ensureRuntime, getRuntime, requireRuntime])
}
