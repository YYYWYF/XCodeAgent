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
  watchEnabled: boolean,
  pageDebug = false
) {
  const [connection, setConnection] = useState(() => initialConnectionState())
  const [readError, setReadError] = useState('')
  const generationRef = useRef(0)
  const [subscriptionRevision, setSubscriptionRevision] = useState(0)
  const readScopeRef = useRef<string>()
  const scopeRef = useRef(workspace)
  scopeRef.current = workspace

  /** 维护操作的失败更新连接状态，错误详情由发起该操作的服务持有。 */
  const reportFailure = useCallback((reason: unknown): void => {
    const generation = ++generationRef.current
    setConnection((current) => !isBackendTransportError(reason)
      ? completeConnectionRequest(current, generation)
      : failConnectionRequest(current, generation, reason instanceof Error ? reason.message : '读取预览服务状态失败'))
  }, [])

  /** 重试先读取权威状态，绝不把断线当作允许重复修复的证据。 */
  const read = useCallback(async (currentThread = threadId): Promise<PreviewRuntimePayload> => {
    const generation = ++generationRef.current
    try {
      const value = await runPreviewRuntime({ workspace, action: 'get', includeLogs, pageDebug: pageDebug || undefined }, { threadId: currentThread })
      if (scopeRef.current === workspace) {
        setReadError('')
        setConnection((current) => completeConnectionRequest(current, generation))
        receive(value, currentThread)
        // 用户正常读取成功后才恢复必要的业务订阅，不用定时器探测服务是否恢复。
        readScopeRef.current = JSON.stringify([workspace, threadId, includeLogs, pageDebug])
        setSubscriptionRevision((revision) => revision + 1)
      }
      return value
    } catch (reason) {
      if (scopeRef.current === workspace) {
        setReadError(backendRequestErrorMessage(reason, '读取预览服务状态失败'))
        setConnection((current) => !isBackendTransportError(reason)
          ? completeConnectionRequest(current, generation)
          : failConnectionRequest(current, generation, String(reason)))
      }
      throw reason
    }
  }, [workspace, threadId, includeLogs, pageDebug, receive])

  useEffect(() => { setReadError('') }, [workspace])

  useEffect(() => {
    const controller = new AbortController()
    const scope = JSON.stringify([workspace, threadId, includeLogs, pageDebug])
    const alreadyRead = readScopeRef.current === scope
    readScopeRef.current = undefined
    /** 工作区首次读取及可见/运行中的预览订阅；失败后停止，等待下一次正常请求。 */
    const watch = async (): Promise<void> => {
      let initialRead = !alreadyRead
      while (!controller.signal.aborted && workspace) {
        if (!initialRead && !watchEnabled) return
        const generation = ++generationRef.current
        try {
          await runPreviewRuntime({ workspace, action: initialRead ? 'get' : 'watch', includeLogs, pageDebug: pageDebug || undefined }, {
            threadId,
            signal: controller.signal,
            onUpdate: (value) => {
              if (controller.signal.aborted) return
              setReadError(value.status === 'failed' ? value.error?.message || '读取预览服务状态失败' : '')
              setConnection((current) => completeConnectionRequest(current, generation))
              receive(value, threadId)
            }
          })
          initialRead = false
        } catch (reason) {
          if (controller.signal.aborted) return
          setReadError(backendRequestErrorMessage(reason, '读取预览服务状态失败'))
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
  }, [workspace, threadId, includeLogs, pageDebug, receive, watchEnabled, subscriptionRevision])
  return { connection, read, reportFailure, readError }
}
