import { useEffect, useRef, useState, type ReactElement } from 'react'
import { useAgentUIState, useOptionalAgentUI } from '@za38.03/agent-ui-runtime'
import { useRuntimeReceipt, useRuntimeResult, runtimeResultLabel } from './useRuntimeResult'
import { useSharedRunContext } from './runtimeContext'
import { requestRuntimeState } from './runtimeRequest'
import type { RuntimeBusinessResult } from './runtimeResults'

/** 在同一会话展示业务结果、原生待办和显式只读查询，不创建新的聊天消息 owner。 */
export function RuntimeBusinessPanel({ agentId, endpoint }: { agentId?: string; endpoint: string }): ReactElement | null {
  const host = useOptionalAgentUI()
  const runtime = useAgentUIState((snapshot) => snapshot.runtime)
  const observed = useRuntimeResult(agentId)
  const receipt = useRuntimeReceipt(agentId)
  const context = useSharedRunContext()
  const [inspected, setInspected] = useState<{ value: RuntimeBusinessResult; recovery: boolean } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [answer, setAnswer] = useState('')
  const operation = useRef<{ abort: () => void } | null>(null)
  const epoch = useRef(0)
  const threadId = runtime?.conversation.id ?? ''
  useEffect(() => {
    epoch.current += 1
    operation.current?.abort()
    operation.current = null
    setInspected(null)
    setBusy(false)
    setError('')
    return (): void => { epoch.current += 1; operation.current?.abort() }
  }, [threadId, endpoint])
  useEffect(() => {
    if (runtime?.run.status === 'running') setInspected(null)
  }, [runtime?.run.status])
  const validInspection = inspected?.value.threadId === threadId ? inspected : null
  const summary = validInspection?.recovery ? validInspection.value : observed
  const result = validInspection?.recovery ? validInspection.value : receipt
  const pending = validInspection ? validInspection.value.pendingInteraction : receipt?.pendingInteraction
  const waiting = Boolean(pending) && (validInspection !== null || observed?.status === 'awaiting_user')
  const running = busy || runtime?.run.status === 'running'

  /** 只在本次用户操作仍属于当前线程时提交展示结果。 */
  async function execute(value?: string | boolean): Promise<void> {
    if (!host || !agentId || !threadId || running) return
    const currentEpoch = ++epoch.current
    setBusy(true)
    setError('')
    try {
      if (value !== undefined && pending && runtime?.interrupts.some((item) => item.id === pending.interactionId)) {
        setInspected(null)
        await host.resumeInterrupts([{ interruptId: pending.interactionId, status: 'resolved', payload: value }])
      } else {
        // 查询不改变官方消息；断流后丢失本地 interrupt 时，显式恢复仍只回答原服务器待办。
        const request = requestRuntimeState({ endpoint, agentId, threadId, context: context.read(),
          ...(value !== undefined && pending ? { response: { interruptId: pending.interactionId,
            status: 'resolved' as const, payload: value } } : {}),
        })
        operation.current = request
        const receipt = await request.result
        if (currentEpoch === epoch.current) setInspected({ value: receipt, recovery: value !== undefined })
      }
      if (currentEpoch === epoch.current) setAnswer('')
    } catch (cause) {
      if (currentEpoch === epoch.current) setError(cause instanceof Error ? cause.message : '请求失败，请查询当前状态。')
    } finally {
      if (currentEpoch === epoch.current) { operation.current = null; setBusy(false) }
    }
  }

  if (!agentId || !runtime) return null
  return <aside className="shared-agent-business" aria-label="业务执行状态">
    {summary && summary.status !== 'idle' && <p role="status" data-runtime-business-status={summary.status}>{runtimeResultLabel(summary)}</p>}
    {result?.error && <p role="alert">{result.error.message}</p>}
    {result?.result !== undefined && result.result !== null && <pre aria-label="业务结果">{JSON.stringify(result.result, null, 2)}</pre>}
    {error && <p role="alert">{error}</p>}
    {validInspection && !validInspection.recovery && <p role="status">{validInspection.value.status === 'failed'
      ? validInspection.value.error?.message : pending ? '已查询到当前待办。' : '当前没有待处理交互。本次查询不确认原操作是否成功。'}</p>}
    {waiting && pending && <div role="group" aria-label="待处理交互">
      <p>{pending.message}</p>
      {Object.keys(pending.payload).length > 0 && <pre>{JSON.stringify(pending.payload, null, 2)}</pre>}
      {pending.kind === 'clarification' ? <>
        <label>补充信息<input value={answer} onChange={(event) => setAnswer(event.target.value)} disabled={running} /></label>
        <button type="button" disabled={running || !answer.trim()} onClick={() => { void execute(answer) }}>提交回答</button>
      </> : <>
        <button type="button" disabled={running} onClick={() => { void execute(true) }}>同意并继续</button>
        <button type="button" disabled={running} onClick={() => { void execute(false) }}>拒绝</button>
      </>}
    </div>}
    <button type="button" disabled={running} onClick={() => { void execute() }}>{busy ? '处理中…' : '查询当前状态'}</button>
  </aside>
}
