import { useCallback, useEffect, useRef, useState } from 'react'
import { PreviewRuntimeBusinessError, runPreviewRuntime } from '../../../service/previewRuntime'
import type { PreviewRuntimePayload } from '../../../service/previewRuntime'
import { completeConnectionRequest, failConnectionRequest, initialConnectionState } from '../../../service/connectionState'

/** 独立订阅预览服务事实，连接结果只影响预览入口，不覆盖 Workflow 的连接状态。 */
export function usePreviewRuntimeConnection(
  workspace: string,
  threadId: string | undefined,
  includeLogs: boolean,
  receive: (value: PreviewRuntimePayload, threadId?: string) => void
) {
  const [connection, setConnection] = useState(() => initialConnectionState())
  const [businessError, setBusinessError] = useState('')
  const generationRef = useRef(0)
  const scopeRef = useRef(workspace)
  scopeRef.current = workspace

  /** 只投影本工作区当前请求的通信错误；业务失败意味着 Backend 已响应。 */
  const reportFailure = useCallback((reason: unknown): void => {
    const generation = ++generationRef.current
    setConnection((current) => reason instanceof PreviewRuntimeBusinessError
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
      }
      return value
    } catch (reason) {
      if (scopeRef.current === workspace) {
        setConnection((current) => reason instanceof PreviewRuntimeBusinessError
          ? completeConnectionRequest(current, generation)
          : failConnectionRequest(current, generation, String(reason)))
      }
      throw reason
    }
  }, [workspace, threadId, includeLogs, receive])

  useEffect(() => {
    setConnection(initialConnectionState())
    setBusinessError('')
    const controller = new AbortController()
    /** 自动重连只恢复订阅，不派发维护或代码修改动作。 */
    const watch = async (): Promise<void> => {
      let initialRead = true
      while (!controller.signal.aborted && workspace) {
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
          initialRead = true
          setBusinessError(reason instanceof PreviewRuntimeBusinessError ? reason.message : '')
          // watch 是长连接，同代次失败必须覆盖此前收到的状态。
          setConnection((current) => reason instanceof PreviewRuntimeBusinessError
            ? completeConnectionRequest(current, generation)
            : failConnectionRequest(current, generation, String(reason)))
          await new Promise<void>((resolve) => window.setTimeout(resolve, 1000))
        }
      }
    }
    void watch()
    return () => controller.abort()
  }, [workspace, threadId, includeLogs, receive])
  return { connection, read, reportFailure, businessError }
}
