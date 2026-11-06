import { useEffect, useState } from 'react'
import { useOptionalAgentUI, type AgentUIHost } from '@za38.03/agent-ui-runtime'
import { observeRuntimeResults, type RuntimeBusinessResult } from './runtimeResults'

interface Observation {
  host: AgentUIHost
  agentId: string
  result: RuntimeBusinessResult
}

/** 订阅唯一公开 Host；身份/Agent 变化立即隔离旧视图，并在提交时释放旧订阅。 */
export function useRuntimeResult(agentId?: string): RuntimeBusinessResult | null {
  const host = useOptionalAgentUI()
  const [observation, setObservation] = useState<Observation | null>(null)
  useEffect(() => {
    if (!host || !agentId) return
    return observeRuntimeResults(host, agentId, (result) => setObservation({ host, agentId, result }))
  }, [host, agentId])
  return observation?.host === host && observation?.agentId === agentId ? observation.result : null
}

/** 展示有界业务状态；不把 wire 结束、等待用户或未知结果标记成业务成功。 */
export function runtimeResultLabel(result: RuntimeBusinessResult): string {
  switch (result.status) {
    case 'idle': return ''
    case 'running': return '正在运行，等待业务结果。'
    case 'completed': return '业务已完成。'
    case 'failed': return '业务执行失败。'
    case 'awaiting_user': return '等待用户处理；当前入口尚未接通恢复操作。'
    case 'unknown': return result.reason === 'run_identity_unavailable'
      ? '业务结果未知：当前连接未提供可核验的运行身份，系统不会自动重发。'
      : '业务结果未知，系统不会自动重发。'
  }
}
