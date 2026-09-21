import { useEffect, useSyncExternalStore } from 'react'
import { readWorkspaceFile } from './workspaceTools'
import {
  startAgentRuntimeDebug,
  type AgentRuntimeDebugResult
} from './agentRuntimeDebug'

export type AgentRuntimeDebugButtonStatus = 'idle' | 'starting' | 'running' | 'failed'

type WorkspaceRuntimeDebugState = {
  status: AgentRuntimeDebugButtonStatus
  startingPromise?: Promise<AgentRuntimeDebugResult>
}

export const AGENT_RUNTIME_DEBUG_STATE_PATH =
  '.xcodeagent/runtime/launch/agent-runtime-debug.json'

const workspaceStates = new Map<string, WorkspaceRuntimeDebugState>()
const listeners = new Set<() => void>()

/** 把工作区路径归一成跨斜杠和大小写一致的存储键。 */
export function agentRuntimeDebugWorkspaceKey(workspaceRoot: string): string {
  return workspaceRoot.trim().replace(/[\\/]+$/, '').replace(/\\/g, '/').toLowerCase()
}

/** 把工作区调试 JSON 的融合 status 映射为按钮展示态。 */
export function mapAgentRuntimeDebugButtonStatus(
  value: unknown
): AgentRuntimeDebugButtonStatus {
  const status = typeof value === 'string' ? value.trim() : ''
  if (status === 'running') return 'running'
  if (
    status === 'cleaning' ||
    status === 'validating' ||
    status === 'installing' ||
    status === 'starting'
  ) {
    return 'starting'
  }
  if (status.endsWith('_failed')) return 'failed'
  return 'idle'
}

/** 读取指定工作区当前按钮状态；无记录时视为未启动。 */
export function getAgentRuntimeDebugStatus(
  workspaceRoot?: string
): AgentRuntimeDebugButtonStatus {
  if (!workspaceRoot) return 'idle'
  return workspaceStates.get(agentRuntimeDebugWorkspaceKey(workspaceRoot))?.status || 'idle'
}

/** 直接写入工作区按钮状态，供测试和本机会话恢复使用。 */
export function setAgentRuntimeDebugStatus(
  workspaceRoot: string,
  status: AgentRuntimeDebugButtonStatus
): void {
  writeWorkspaceState(agentRuntimeDebugWorkspaceKey(workspaceRoot), { status })
}

/** 订阅工作区 Runtime 按钮状态，供详情页卸载后再挂载时复用同一份事实。 */
export function subscribeAgentRuntimeDebugStore(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/** 项目删除或工作区清理时丢弃对应 Runtime 按钮状态。 */
export function clearAgentRuntimeDebugStore(workspaceRoot?: string): void {
  if (!workspaceRoot) {
    workspaceStates.clear()
    emitAgentRuntimeDebugStore()
    return
  }
  workspaceStates.delete(agentRuntimeDebugWorkspaceKey(workspaceRoot))
  emitAgentRuntimeDebugStore()
}

/** 启动工作区 Runtime，并在同一工作区的所有详情页同步按钮状态。 */
export function startWorkspaceAgentRuntimeDebug(
  workspaceRoot: string
): Promise<AgentRuntimeDebugResult> {
  const key = agentRuntimeDebugWorkspaceKey(workspaceRoot)
  const current = workspaceStates.get(key)
  if (current?.startingPromise) return current.startingPromise
  const startingPromise = startAgentRuntimeDebug(workspaceRoot)
    .then((result) => {
      writeWorkspaceState(key, { status: 'running' })
      return result
    })
    .catch((error: unknown) => {
      writeWorkspaceState(key, { status: 'failed' })
      throw error
    })
  writeWorkspaceState(key, { status: 'starting', startingPromise })
  return startingPromise
}

/** 从工作区状态文件恢复按钮态，不缓存 debugToken 等敏感字段。 */
export async function hydrateAgentRuntimeDebugStatus(
  workspaceRoot: string
): Promise<void> {
  const key = agentRuntimeDebugWorkspaceKey(workspaceRoot)
  if (workspaceStates.get(key)?.startingPromise) return
  try {
    const result = await readWorkspaceFile({
      workspace_root: workspaceRoot,
      path: AGENT_RUNTIME_DEBUG_STATE_PATH,
      max_lines: 80,
      max_chars: 4_000
    })
    if (workspaceStates.get(key)?.startingPromise) return
    writeWorkspaceState(key, {
      status: mapAgentRuntimeDebugButtonStatus(readWorkspaceDebugStatus(result.content))
    })
  } catch {
    if (workspaceStates.get(key)?.startingPromise || workspaceStates.has(key)) return
    writeWorkspaceState(key, { status: 'idle' })
  }
}

/** 工作区级 Runtime 按钮状态：切页、换智能体都读取同一份记录。 */
export function useAgentRuntimeDebugStatus(
  workspaceRoot?: string
): AgentRuntimeDebugButtonStatus {
  const status = useSyncExternalStore(
    subscribeAgentRuntimeDebugStore,
    () => getAgentRuntimeDebugStatus(workspaceRoot),
    () => getAgentRuntimeDebugStatus(workspaceRoot)
  )
  useEffect(() => {
    if (!workspaceRoot) return
    void hydrateAgentRuntimeDebugStatus(workspaceRoot)
  }, [workspaceRoot])
  return status
}

/** 仅解析状态字段，丢弃 Token 和其他调试证据。 */
function readWorkspaceDebugStatus(content: string): unknown {
  try {
    const payload = JSON.parse(content) as { status?: unknown }
    return payload?.status
  } catch {
    return undefined
  }
}

/** 写入指定工作区状态并通知全部订阅者。 */
function writeWorkspaceState(key: string, next: WorkspaceRuntimeDebugState): void {
  workspaceStates.set(key, next)
  emitAgentRuntimeDebugStore()
}

/** 通知所有详情页按最新工作区状态重绘按钮。 */
function emitAgentRuntimeDebugStore(): void {
  listeners.forEach((listener) => listener())
}
