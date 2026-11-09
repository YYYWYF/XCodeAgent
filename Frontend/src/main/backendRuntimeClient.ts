import { randomUUID } from 'node:crypto'
import { HttpAgent } from '@ag-ui/client'

export type RuntimeIdentity = { baseUrl: string; backendInstanceId: string; controlToken: string }
export type RuntimeAction = 'attach' | 'detach' | 'prepare_exit' | 'commit_exit' | 'cancel_exit'
export type RuntimeResult = { state: 'open' | 'prepared' | 'exiting' | 'busy'; ticket?: string }

/** 通过独立 AG-UI 控制流提交租约和退场请求，凭据只存在本机私有登记文件中。 */
export async function controlBackendRuntime(
  runtime: RuntimeIdentity, clientId: string, action: RuntimeAction, ticket = ''
): Promise<RuntimeResult> {
  const agent = new HttpAgent({ url: `${runtime.baseUrl}/backend-runtime/run`, threadId: randomUUID() })
  let result: RuntimeResult | undefined
  let failed = false
  let finished = false
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    await Promise.race([
      agent.runAgent({ runId: randomUUID(), forwardedProps: { backendRuntime: {
        action, clientId, ticket, backendInstanceId: runtime.backendInstanceId, controlToken: runtime.controlToken
      } } }, {
        onCustomEvent: ({ event }) => {
          if (event.name !== 'backend-runtime') return
          const value = event.value as Record<string, unknown> | undefined
          if (!value || value.schemaVersion !== 1 || value.status !== 'completed' ||
            !['open', 'prepared', 'exiting', 'busy'].includes(String(value.state))) { failed = true; return }
          result = { state: value.state as RuntimeResult['state'],
            ticket: typeof value.ticket === 'string' ? value.ticket : undefined }
        },
        onRunFailed: () => { failed = true },
        onRunFinishedEvent: () => { finished = true }
      }),
      new Promise<never>((_resolve, reject) => {
        timer = setTimeout(() => { agent.abortRun(); reject(new Error('后台交接超时，已保留原任务，请稍后重试。')) }, 5000)
      })
    ])
    if (failed || !finished || !result) throw new Error('后台交接未完成，已保留原任务，请重新同步。')
    return result
  } finally {
    clearTimeout(timer)
  }
}

/** 只观察原 PID 是否退出；超时保留现场，绝不强杀未知或忙碌后台。 */
export async function waitBackendExit(pid: number, timeoutMs = 30000): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (true) {
    try { process.kill(pid, 0) }
    catch (error) { if ((error as NodeJS.ErrnoException).code === 'ESRCH') return; throw error }
    if (Date.now() >= deadline) throw new Error('原后台仍在收口，已暂停更新，请稍后重新打开客户端。')
    await new Promise((resolve) => setTimeout(resolve, 100))
  }
}

/** 空闲实例先关闭接收入口，再执行清理和退出；准备失败不改变原任务。 */
export async function retireBackendRuntime(
  runtime: RuntimeIdentity & { pid: number }, clientId: string, cleanup: () => Promise<void>
): Promise<void> {
  const prepared = await controlBackendRuntime(runtime, clientId, 'prepare_exit')
  if (prepared.state === 'exiting') { await waitBackendExit(runtime.pid); return }
  if (prepared.state !== 'prepared' || !prepared.ticket) throw new Error('后台仍有任务或其它客户端，请完成任务后再更新。')
  let committed = false
  try {
    await cleanup()
    const result = await controlBackendRuntime(runtime, clientId, 'commit_exit', prepared.ticket)
    if (result.state !== 'exiting') throw new Error('后台清理尚未完成，请稍后再更新。')
    committed = true
    await waitBackendExit(runtime.pid)
  } catch (error) {
    if (!committed) await controlBackendRuntime(runtime, clientId, 'cancel_exit', prepared.ticket).catch(() => undefined)
    throw error
  }
}
