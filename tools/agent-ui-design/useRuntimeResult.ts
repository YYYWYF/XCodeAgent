import { useEffect, useState } from 'react'
import { useOptionalAgentUI, type AgentUIHost } from '@za38.03/agent-ui-runtime'
import {
  observeRuntimeReceipts,
  observeRuntimeResults,
  type RuntimeBusinessResult,
  type RuntimeBusinessSummary,
} from './runtimeResults'

interface Observation<T> {
  host: AgentUIHost
  agentId: string
  result: T
}

/** 订阅指定观察器，并按 Host 与 Agent 身份隔离视图状态。 */
function useRuntimeObservation<T>(agentId: string | undefined,
  observe: (host: AgentUIHost, agentId: string, publish: (result: T) => void) => () => void): T | null {
  const host = useOptionalAgentUI()
  const [observation, setObservation] = useState<Observation<T> | null>(null)
  useEffect(() => {
    if (!host || !agentId) return
    return observe(host, agentId, (result) => setObservation({ host, agentId, result }))
  }, [host, agentId, observe])
  return observation?.host === host && observation?.agentId === agentId ? observation.result : null
}

/** 只提供不含业务正文的运行摘要。 */
export function useRuntimeResult(agentId?: string): RuntimeBusinessSummary | null {
  return useRuntimeObservation(agentId, observeRuntimeResults)
}

/** 仅供业务面板展示经校验的结果、错误和待办内容。 */
export function useRuntimeReceipt(agentId?: string): RuntimeBusinessResult | null {
  return useRuntimeObservation(agentId, observeRuntimeReceipts)
}

/** 展示有界业务状态；不把 wire 结束、等待用户或未知结果标记成业务成功。 */
export function runtimeResultLabel(result: RuntimeBusinessResult): string {
  switch (result.status) {
    case 'idle': return ''
    case 'running': return '正在运行，等待业务结果。'
    case 'completed': return '业务已完成。'
    case 'failed': return '业务执行失败。'
    case 'awaiting_user': return '等待用户处理，请回答或确认当前待办。'
    case 'unknown': return '业务结果未知，请查询当前状态；系统不会自动重发。'
  }
}
