import { useCallback, useEffect, useRef, useState } from 'react'
import { AgUiBusinessError } from '../../../service/agUiBusinessError'
import { isAuthenticationFailure } from '../../../service/authentication'
import { beginConnectionRequest, initialConnectionState } from '../../../service/connectionState'

export type EndpointRecoveryReport = {
  workspaceRoot: string
  scopeKey: string
  source: 'binding' | 'detail' | 'advanced'
  slot: string
  reason?: unknown
  retry?: () => Promise<void> | void
}
export type EndpointRecoveryReporter = (report: EndpointRecoveryReport) => void

/** 以工作区和 Endpoint 身份绑定兜底，切换目标后不借用另一个接口的错误。 */
export function endpointRecoveryScope(workspaceRoot: string, target?: { apiContractId: string; endpointId: string }): string {
  return JSON.stringify([workspaceRoot, target?.apiContractId || '', target?.endpointId || ''])
}

/** 业务响应与认证拒绝都说明 Backend 已响应，不能误报为停服。 */
export function endpointFailureIsTransport(reason: unknown): boolean {
  return !(reason instanceof AgUiBusinessError) && !isAuthenticationFailure(reason)
}

/** 保存当前面板的独立错误归属，只重试原只读校准，不派发 Graph 或保存动作。 */
export function useEndpointDesignRecovery(workspaceRoot: string, scopes: string[], sources: string[]) {
  const [reports, setReports] = useState<Record<string, EndpointRecoveryReport & { sequence: number }>>({})
  const sequence = useRef(0)
  const currentWorkspace = useRef(workspaceRoot)
  currentWorkspace.current = workspaceRoot
  const [retrying, setRetrying] = useState(false)
  const locked = useRef(false)
  useEffect(() => { setReports({}) }, [workspaceRoot])
  const report = useCallback<EndpointRecoveryReporter>((value) => {
    if (currentWorkspace.current !== value.workspaceRoot) return
    const requestSequence = ++sequence.current
    setReports((current) => {
      const next = { ...current }
      if (value.reason === undefined) {
        for (const [key, saved] of Object.entries(next)) {
          if (saved.scopeKey === value.scopeKey && saved.source === value.source && (value.slot === '*' || saved.slot === value.slot)) delete next[key]
        }
      } else next[JSON.stringify([value.scopeKey, value.source, value.slot])] = { ...value, sequence: requestSequence }
      return next
    })
  }, [])
  const issue = Object.values(reports).filter((value) => value.workspaceRoot === workspaceRoot && scopes.includes(value.scopeKey) && sources.includes(value.source))
    .sort((left, right) => right.sequence - left.sequence)[0]
  const connection = issue && endpointFailureIsTransport(issue.reason)
    ? { ...initialConnectionState(), status: 'unavailable' as const, lastError: issue.reason instanceof Error ? issue.reason.message : String(issue.reason) }
    : initialConnectionState(true)
  /** 同一时间只校准一次，失败继续由原作用域报告，旧面板不会覆盖新面板。 */
  const retry = async (): Promise<void> => {
    if (!issue?.retry || locked.current) return
    locked.current = true
    setRetrying(true)
    try { await issue.retry() }
    catch (reason) { report({ ...issue, reason }) }
    finally { locked.current = false; setRetrying(false) }
  }
  return { report, issue, retry, retrying, connection: retrying ? beginConnectionRequest(connection, sequence.current) : connection }
}
