import type { BackendConnectionState } from '../../../shared/backendConnection'

let current: BackendConnectionState | undefined
let initializing: Promise<void> | undefined
const origins = new Set<string>()
const listeners = new Set<(state: BackendConnectionState) => void>()

/** 让旧连接的回调只能写入其所属传输代次。 */
export function backendConnectionRevision(): number { return current?.revision ?? 0 }

/** 返回实时接入地址；启动参数只用于首次 IPC 快照到达之前。 */
export function currentBackendBaseUrl(fallback = 'http://127.0.0.1:8000'): string {
  return (current?.baseUrl || window.devAgentStudio?.agentBaseUrl || fallback).replace(/\/$/, '')
}

/** 只重定向当前桌面实例曾使用的 origin，保留独立外部 Agent 的目标。 */
export function resolveBackendUrl(url: string): string {
  if (!current) return url
  try {
    const parsed = new URL(url)
    if (origins.has(parsed.origin)) return `${current.baseUrl.replace(/\/$/, '')}${parsed.pathname}${parsed.search}${parsed.hash}`
  } catch { /* 相对地址继续使用原有开发代理。 */ }
  return url
}

/** 按主进程代次合并快照，拒绝冷重载补读覆盖更新后的地址。 */
function receive(state: BackendConnectionState): void {
  if (current && state.revision <= current.revision) return
  const initial = window.devAgentStudio?.agentBaseUrl
  if (initial) origins.add(new URL(initial).origin)
  origins.add(new URL(state.baseUrl).origin)
  current = state
  listeners.forEach((listener) => listener(state))
}

/** 先订阅后补读，保证页面启动和运行中换后台都使用同一个连接来源。 */
export async function initializeBackendConnection(): Promise<void> {
  if (initializing) return initializing
  const api = window.devAgentStudio?.backendConnection
  if (!api) return
  api.onStateChanged(receive)
  initializing = api.getState().then(receive)
  return initializing
}

/** 订阅连接代次；调用方仅使旧请求失效并读取现场，不自动发起业务动作。 */
export function subscribeBackendConnection(listener: (state: BackendConnectionState) => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}
