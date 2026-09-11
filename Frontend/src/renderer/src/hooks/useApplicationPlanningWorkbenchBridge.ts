import { useCallback, useEffect, useRef } from 'react'
import type { WorkflowRevisionContinuationHandoff } from '../service/applicationPagePlanning'
import type { WorkflowRunPayload } from '../typings'

/** 将 Runtime 输出接入当前工作台历史，并保留正式修订的开发续接交接。 */
export function useApplicationPlanningWorkbenchBridge(activePlanningThreadId?: string) {
  const revisionContinuationByAppRef = useRef<
    Record<string, ((handoff: WorkflowRevisionContinuationHandoff) => Promise<void>) | undefined>
  >({})
  const pendingRevisionContinuationByAppRef = useRef<
    Record<
      string,
      | {
          handoff: WorkflowRevisionContinuationHandoff
          promise: Promise<void>
          reject: (reason?: unknown) => void
          resolve: () => void
        }
      | undefined
    >
  >({})

  /** 将规划 Graph 签发的 continuation 转交给工作台；句柄尚未挂载时保留同一次交接。 */
  const dispatchRevisionContinuation = useCallback(
    (applicationId: string, handoff: WorkflowRevisionContinuationHandoff): Promise<void> => {
      const handler = revisionContinuationByAppRef.current[applicationId]
      if (handler) return handler(handoff)

      const pending = pendingRevisionContinuationByAppRef.current[applicationId]
      if (
        pending?.handoff.continuation.changeId === handoff.continuation.changeId &&
        pending.handoff.continuation.token === handoff.continuation.token
      ) {
        return pending.promise
      }
      pending?.reject(new Error('revision continuation 已被更新的服务端状态替代。'))

      let resolvePending!: () => void
      let rejectPending!: (reason?: unknown) => void
      const promise = new Promise<void>((resolve, reject) => {
        resolvePending = resolve
        rejectPending = reject
      })
      pendingRevisionContinuationByAppRef.current[applicationId] = {
        handoff,
        promise,
        reject: rejectPending,
        resolve: resolvePending
      }
      return promise
    },
    []
  )

  // 规划流式数据注入句柄：由工作台 AiChatPanel 注册，Runtime 发布 onContent/onWorkflow 时调用，
  // 把规划流式内容注入工作台 MessageList（设计阶段产品 Agent 对话 + 工作流卡片）。
  const planningStreamRef = useRef<
    ((chunk: { content?: string; workflow?: WorkflowRunPayload }) => void) | null
  >(null)
  // 当前活动工作台的规划线程标识：只有匹配该 threadId 的规划流式才注入工作台，
  // 避免后台其他应用规划的流式 chunk 串入当前工作台对话。
  const activePlanningThreadIdRef = useRef<string | undefined>(undefined)
  // 注入句柄注册前缓存的流式 chunk（带 threadId），注册后按当前工作台 threadId 回放，
  // 避免后台其他应用规划的 chunk 串入当前工作台对话。
  const pendingPlanningChunksRef = useRef<
    Array<{ threadId: string; chunk: { content?: string; workflow?: WorkflowRunPayload } }>
  >([])
  /** 按原规划线程把 Runtime 输出交给当前工作台，未挂载时暂存历史片段。 */
  const deliverPlanningChunk = useCallback(
    (threadId: string, chunk: { content?: string; workflow?: WorkflowRunPayload }) => {
      // 只注入当前活动工作台对应线程的流式，丢弃其他后台规划的 chunk。
      // 但当前工作台 threadId 尚未确定（ref=undefined，新建应用首次进入）时不丢弃，
      // 缓存待 ref 就绪后回放——避免最早的 workflow 快照丢失导致一直 loading。
      const activeThreadId = activePlanningThreadIdRef.current
      if (activeThreadId !== undefined && activeThreadId !== threadId) return
      const stream = planningStreamRef.current
      if (stream) {
        stream(chunk)
      } else {
        pendingPlanningChunksRef.current.push({ threadId, chunk })
      }
    },
    []
  )

  // 工作台注册规划流式注入句柄；稳定化避免 AiChatPanel 注入 effect 反复触发。
  const handlePlanningStreamReady = useCallback(
    (inject: ((chunk: { content?: string; workflow?: WorkflowRunPayload }) => void) | null) => {
      planningStreamRef.current = inject
      const activeThreadId = activePlanningThreadIdRef.current
      // 注册后回放工作台挂载前缓存的 chunk，只回放当前工作台 threadId 的。
      // activeThreadId 尚未确定时不回放，由 activePlanningThreadId effect 在 threadId 就绪后回放。
      if (inject && activeThreadId) {
        const pending = pendingPlanningChunksRef.current
        if (pending.length) {
          const matched = pending.filter((item) => item.threadId === activeThreadId)
          pendingPlanningChunksRef.current = []
          for (const item of matched) {
            inject(item.chunk)
          }
        }
      }
    },
    []
  )

  useEffect(() => {
    activePlanningThreadIdRef.current = activePlanningThreadId
    // threadId 就绪后，如果 stream 已注册且有待回放的缓存，按 threadId 回放。
    // 解决新建应用首次进入时 ref=undefined 导致 handlePlanningStreamReady 过滤掉所有缓存的问题。
    if (activePlanningThreadId && planningStreamRef.current) {
      const pending = pendingPlanningChunksRef.current
      if (pending.length) {
        const matched = pending.filter((item) => item.threadId === activePlanningThreadId)
        pendingPlanningChunksRef.current = []
        for (const item of matched) {
          planningStreamRef.current(item.chunk)
        }
      }
    }
  }, [activePlanningThreadId])

  /** 注册工作台的修订续接接收者，并交付同一应用等待中的服务端凭据。 */
  const registerRevisionContinuation = useCallback(
    (
      applicationId: string,
      handler?: (handoff: WorkflowRevisionContinuationHandoff) => Promise<void>
    ): void => {
      revisionContinuationByAppRef.current[applicationId] = handler
      const pending = pendingRevisionContinuationByAppRef.current[applicationId]
      if (!handler || !pending) return
      delete pendingRevisionContinuationByAppRef.current[applicationId]
      void handler(pending.handoff).then(pending.resolve, pending.reject)
    },
    []
  )

  return {
    deliverPlanningChunk,
    dispatchRevisionContinuation,
    handlePlanningStreamReady,
    registerRevisionContinuation
  }
}
