import { useCallback, useState } from 'react'

/** 按当前会话记录权威状态是否读取成功，切换会话时不能沿用上一会话的就绪标记。 */
export function useSessionStatusReadiness(sessionKey: string | undefined): {
  ready: boolean
  error?: string
  settle: (error?: string) => void
} {
  const [result, setResult] = useState<{ key?: string; ready: boolean; error?: string }>({
    key: sessionKey,
    ready: !sessionKey
  })
  // 切换即撤销旧就绪事实；A→B→A 即使 B 尚未返回，也必须重新等待 A 的本次读取。
  if (result.key !== sessionKey) setResult({ key: sessionKey, ready: !sessionKey })
  const current = result.key === sessionKey ? result : undefined
  /** 由已通过会话和请求代次校验的读取回执收口，不用本地历史推断就绪。 */
  const settle = useCallback(
    (error?: string): void => {
      if (sessionKey)
        setResult((current) =>
          current.key === sessionKey ? { key: sessionKey, ready: !error, error } : current
        )
    },
    [sessionKey]
  )
  return {
    ready: !sessionKey || Boolean(current?.ready),
    error: current?.error,
    settle
  }
}
