import { useCallback, useEffect, useRef, useState } from 'react'
import type { EndpointDesignDetail } from '../../../typings'
import { requestEndpointDesignDetail } from '../../../service/endpointDesigns'
import { endpointRecoveryScope, type EndpointRecoveryReporter } from './useEndpointDesignRecovery'

type Target = { apiContractId: string; endpointId: string }

/** 管理右侧 Endpoint 设计详情查询，并丢弃已经过期的异步响应。 */
export function useEndpointDesignDetail(
  workspaceRoot: string | undefined,
  target: Target | undefined,
  refreshKey?: number,
  onRecovery?: EndpointRecoveryReporter
): {
  detail?: EndpointDesignDetail
  loading: boolean
  error?: string
  reload: () => void
} {
  const [detail, setDetail] = useState<EndpointDesignDetail>()
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string>()
  const [reloadToken, setReloadToken] = useState(0)
  const requestSequence = useRef(0)
  const recoveryRef = useRef(onRecovery)
  recoveryRef.current = onRecovery
  const reload = useCallback(() => setReloadToken((value) => value + 1), [])
  const apiContractId = target?.apiContractId
  const endpointId = target?.endpointId

  useEffect(() => {
    const requestId = requestSequence.current + 1
    requestSequence.current = requestId
    if (!workspaceRoot || !apiContractId || !endpointId) {
      setDetail(undefined)
      setError(undefined)
      setLoading(false)
      return
    }
    setLoading(true)
    setError(undefined)
    const scopeKey = endpointRecoveryScope(workspaceRoot, { apiContractId, endpointId })
    requestEndpointDesignDetail(workspaceRoot, apiContractId, endpointId)
      .then((nextDetail) => {
        if (requestSequence.current === requestId) {
          setDetail(nextDetail)
          recoveryRef.current?.({ workspaceRoot, scopeKey, source: 'detail', slot: 'get' })
        }
      })
      .catch((cause: unknown) => {
        if (requestSequence.current === requestId) {
          setDetail(undefined)
          setError(cause instanceof Error ? cause.message : '读取 Endpoint API 映射结果失败。')
          recoveryRef.current?.({ workspaceRoot, scopeKey, source: 'detail', slot: 'get', reason: cause, retry: reload })
        }
      })
      .finally(() => {
        if (requestSequence.current === requestId) setLoading(false)
      })
    return () => {
      requestSequence.current += 1
    }
  }, [apiContractId, endpointId, refreshKey, reloadToken, workspaceRoot])

  // 刷新期间保留原失败；只有真正离开接口时才解除该详情的归属。
  useEffect(() => () => {
    if (workspaceRoot && apiContractId && endpointId) recoveryRef.current?.({ workspaceRoot,
      scopeKey: endpointRecoveryScope(workspaceRoot, { apiContractId, endpointId }), source: 'detail', slot: '*' })
  }, [workspaceRoot, apiContractId, endpointId])

  return { detail, loading, error, reload }
}
