import { randomUUID } from '@ag-ui/client'
import { useEffect, useRef, useState } from 'react'
import { runBackendLogs } from '../../service/backendLogs'
import type { BackendLogsSnapshot } from '../../service/backendLogs'

/** 等待短暂重连间隔，关闭抽屉时立即释放计时器和监听。 */
function retryDelay(signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    /** 清理重连等待，确保取消后不会再建立订阅。 */
    const finish = (): void => {
      clearTimeout(timer)
      signal.removeEventListener('abort', finish)
      resolve()
    }
    const timer = setTimeout(finish, 2000)
    signal.addEventListener('abort', finish, { once: true })
    if (signal.aborted) finish()
  })
}

/** 管理唯一日志读取循环，暂停、刷新和卸载都会取消旧请求。 */
export function useBackendLogs(autoRefresh: boolean): {
  snapshot?: BackendLogsSnapshot
  loading: boolean
  error: string
  refresh: () => void
} {
  const [snapshot, setSnapshot] = useState<BackendLogsSnapshot>()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [generation, setGeneration] = useState(0)
  const threadId = useRef(randomUUID())

  useEffect(() => {
    const controller = new AbortController()
    const { signal } = controller
    setLoading(true)
    setError('')
    /** 旧请求即使延迟到达也不能覆盖刷新后的日志或错误状态。 */
    const accept = (value: BackendLogsSnapshot): void => {
      if (signal.aborted) return
      setSnapshot(value)
      setLoading(false)
      setError('')
    }
    /** 首先读取快照，再按需续订；失败保留最后一次内容并重试。 */
    const read = async (): Promise<void> => {
      let action: 'get' | 'watch' = 'get'
      do {
        try {
          await runBackendLogs(action, { threadId: threadId.current, signal, onUpdate: accept })
          action = 'watch'
        } catch (cause) {
          if (signal.aborted) return
          setError(cause instanceof Error ? cause.message : '读取后端日志失败。')
          action = 'get'
          if (autoRefresh) await retryDelay(signal)
        } finally {
          if (!signal.aborted) setLoading(false)
        }
      } while (autoRefresh && !signal.aborted)
    }
    void read()
    return () => controller.abort()
  }, [autoRefresh, generation])

  return { snapshot, loading, error, refresh: () => setGeneration((value) => value + 1) }
}
