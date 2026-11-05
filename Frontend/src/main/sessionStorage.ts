import createWorker from './sessionStorageWorker?nodeWorker'
import type { SessionHistoryPage } from '../shared/sessionHistory'

type RecordValue = Record<string, unknown>
type Pending = { resolve: (value: unknown) => void; reject: (error: Error) => void }
let worker: ReturnType<typeof createWorker> | undefined
let sequence = 0
const pending = new Map<number, Pending>()

/** 有界尾读与流式保存移入独立 Worker，避免阻塞 Electron 主进程。 */
function request<T>(payload: RecordValue): Promise<T> {
  if (!worker) {
    const current = createWorker({})
    worker = current
    current.unref()
    current.on('message', ({ id, error, result }) => {
      const entry = pending.get(id)
      pending.delete(id)
      if (error) entry?.reject(new Error(error))
      else entry?.resolve(result)
    })
    current.on('error', (error) => {
      if (worker === current) failPending(error)
    })
    current.on('exit', () => {
      if (worker === current) failPending(new Error('会话存储进程已退出，请重试。'))
    })
  }
  return new Promise<T>((resolve, reject) => {
    const id = ++sequence
    pending.set(id, { resolve: (value) => resolve(value as T), reject })
    worker!.postMessage({ ...payload, id })
  })
}

/** Worker 故障显式拒绝请求；不清空内存正文，也不把失败伪装为空历史。 */
function failPending(error: Error): void {
  worker = undefined
  for (const entry of pending.values()) entry.reject(error)
  pending.clear()
}

/** 读取本地会话的派生摘要。 */
export function readStoredSessionSummary(file: string): Promise<RecordValue> {
  return request({ operation: 'summary', file })
}

/** 从本地历史尾部读取一页，不扫描或回填窗口外的控制消息。 */
export function readStoredSession(
  file: string,
  before?: number,
  limit?: number
): Promise<{
  session: RecordValue
  history: SessionHistoryPage
}> {
  return request({ operation: 'read', file, before, limit })
}

/** 顺序保存已加载范围；原 JSON 仍是唯一正文源，索引可随时重建。 */
export function saveStoredSession(
  file: string,
  session: unknown,
  loadedMessageIds?: number[]
): Promise<RecordValue> {
  return request({ operation: 'save', file, session, loadedMessageIds })
}
