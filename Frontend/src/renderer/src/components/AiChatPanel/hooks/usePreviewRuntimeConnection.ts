import { useCallback, useEffect, useRef, useState } from 'react'
import { runPreviewRuntime } from '../../../service/previewRuntime'
import type { PreviewRuntimePayload } from '../../../service/previewRuntime'
import { completeConnectionRequest, failConnectionRequest, initialConnectionState } from '../../../service/connectionState'
import { backendRequestErrorMessage, isBackendTransportError } from '../../../service/backendRequestFailure'

/** 独立订阅预览服务事实，连接结果只影响预览入口，不覆盖 Workflow 的连接状态。 */
export function usePreviewRuntimeConnection(
  workspace: string,
  threadId: string | undefined,
  includeLogs: boolean,
  receive: (value: PreviewRuntimePayload, threadId?: string) => void,
  watchEnabled: boolean
) {
  const [connection, setConnection] = useState(() => initialConnectionState())
  const [businessError, setBusinessError] = useState('')
  const generationRef = useRef(0)
  const [subscriptionRevision, setSubscriptionRevision] = useState(0)
  const readScopeRef = useRef<string>()
  const scopeRef = useRef(workspace)
  scopeRef.current = workspace

  /** 只投影本工作区当前请求的通信错误；业务失败意味着 Backend 已响应。 */
  const reportFailure = useCallback((reason: unknown): void => {
    const generation = ++generationRef.current
    setBusinessError(isBackendTransportError(reason) ? '' : backendRequestErrorMessage(reason, '读取预览服务状态失败'))
    setConnection((current) => !isBackendTransportError(reason)
      ? completeConnectionRequest(current, generation)
      : failConnectionRequest(current, generation, reason instanceof Error ? reason.message : '读取预览服务状态失败'))
  }, [])

  /** 重试先读取权威状态，绝不把断线当作允许重复修复的证据。 */
  const read = useCallback(async (currentThread = threadId): Promise<PreviewRuntimePayload> => {
    const generation = ++generationRef.current
    try {
      const value = await runPreviewRuntime({ workspace, action: 'get', includeLogs }, { threadId: currentThread })
      if (scopeRef.current === workspace) {
        setBusinessError('')
        setConnection((current) => completeConnectionRequest(current, generation))
        receive(value, currentThread)
        // 用户正常读取成功后才恢复必要的业务订阅，不用定时器探测服务是否恢复。
        readScopeRef.current = JSON.stringify([workspace, threadId, includeLogs])
        setSubscriptionRevision((revision) => revision + 1)
      }
      return value
    } catch (reason) {
      if (scopeRef.current === workspace) {
        setBusinessError(isBackendTransportError(reason) ? '' : backendRequestErrorMessage(reason, '读取预览服务状态失败'))
        setConnection((current) => !isBackendTransportError(reason)
          ? completeConnectionRequest(current, generation)
          : failConnectionRequest(current, generation, String(reason)))
      }
      throw reason
    }
  }, [workspace, threadId, includeLogs, receive])

  useEffect(() => {
    const controller = new AbortController()
    const scope = JSON.stringify([workspace, threadId, includeLogs])
    const alreadyRead = readScopeRef.current === scope
    readScopeRef.current = undefined
    /** 工作区首次读取及可见/运行中的预览订阅；失败后停止，等待下一次正常请求。 */
    const watch = async (): Promise<void> => {
      let initialRead = !alreadyRead
      while (!controller.signal.aborted && workspace) {
        if (!initialRead && !watchEnabled) return
        const generation = ++generationRef.current
        try {
          await runPreviewRuntime({ workspace, action: initialRead ? 'get' : 'watch', includeLogs }, {
            threadId,
            signal: controller.signal,
            onUpdate: (value) => {
              if (controller.signal.aborted) return
              setBusinessError(value.status === 'failed' ? value.error?.message || '读取预览服务状态失败' : '')
              setConnection((current) => completeConnectionRequest(current, generation))
              receive(value, threadId)
            }
          })
          initialRead = false
        } catch (reason) {
          if (controller.signal.aborted) return
          setBusinessError(isBackendTransportError(reason) ? '' : backendRequestErrorMessage(reason, '读取预览服务状态失败'))
          // watch 是长连接，同代次失败必须覆盖此前收到的状态。
          setConnection((current) => !isBackendTransportError(reason)
            ? completeConnectionRequest(current, generation)
            : failConnectionRequest(current, generation, String(reason)))
          return
        }
      }
    }
    void watch()
    return () => controller.abort()
  }, [workspace, threadId, includeLogs, receive, watchEnabled, subscriptionRevision])
  return { connection, read, reportFailure, businessError }
}
