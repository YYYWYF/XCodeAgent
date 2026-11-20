import { createContext, useContext, useEffect, useRef, useState, type ReactElement, type ReactNode } from 'react'
import { useAgentUIState, useOptionalAgentUI } from '@za38.03/agent-ui-runtime'
import { useRuntimeReceipt, useRuntimeResult, runtimeResultLabel } from './useRuntimeResult'
import { useSharedRunContext } from './runtimeContext'
import { requestRuntimeState } from './runtimeRequest'
import type { RuntimeBusinessResult } from './runtimeResults'
import { ConversationTransientMessages, createConversationTransientMessageService, type ConversationTransientMessageService } from '@za38.03/agent-ui-plugins'
import { BusinessValue, hasBusinessValue } from './businessValue'

type ActionState = '处理中' | '已确认' | '已取消' | '已回答' | '已失效' | '结果未知'
interface Step {
  key: string
  noticeId: string
  threadId: string
  value: RuntimeBusinessResult
  context: string
  action?: ActionState
}
interface Cards {
  notices: ConversationTransientMessageService
  steps: readonly Step[]
  threadId: string
  running: boolean
  currentContext: string
  error: string
  inspect: () => void
  respond: (step: Step, value: string | boolean) => void
  resultSchema?: Record<string, unknown>
}
const BusinessCards = createContext<Cards | null>(null)

/** 在稳定 Host 内保存业务步骤；只消费已校验回执，不持有或重建聊天消息。 */
export function RuntimeBusinessProvider({ agentId, endpoint, resultSchema, children }:
  { agentId?: string; endpoint: string; resultSchema?: Record<string, unknown>; children: ReactNode }): ReactElement {
  const host = useOptionalAgentUI()
  const runtime = useAgentUIState((snapshot) => snapshot.runtime)
  const observed = useRuntimeResult(agentId)
  const receipt = useRuntimeReceipt(agentId)
  const context = useSharedRunContext()
  const [notices] = useState(createConversationTransientMessageService)
  const anchors = useRef(new Map<string, string>())
  const [steps, setSteps] = useState<Step[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const operation = useRef<{ abort: () => void } | null>(null)
  const lock = useRef(false)
  const epoch = useRef(0)
  const runContext = useRef(new Map<string, string>())
  const threadId = runtime?.conversation.id ?? ''
  const thread = useRef(threadId)
  thread.current = threadId
  const currentContext = JSON.stringify(context.read())
  const running = busy || runtime?.run.status === 'running'

  /** 将回执固定到本轮最后一条官方消息；以后切页或追加消息不移动卡片。 */
  function record(value: RuntimeBusinessResult, sourceContext?: string): void {
    const key = value.runId || `unknown:${runtime?.messages.at(-1)?.id ?? 'start'}`
    const identity = `${value.threadId}:${key}`
    let noticeId = anchors.current.get(identity)
    if (!noticeId) {
      noticeId = notices.addMessage({ role: 'assistant', text: '业务执行状态' })
      anchors.current.set(identity, noticeId)
    }
    const step: Step = { key, noticeId,
      threadId: value.threadId,
      value, context: sourceContext ?? runContext.current.get(value.threadId) ?? JSON.stringify(context.read()) }
    setSteps((previous) => {
      const existing = previous.find((item) => item.threadId === step.threadId && item.key === step.key)
      return existing ? previous.map((item) => item === existing ? { ...item, value } : item)
        : [...previous, step]
    })
  }
  useEffect(() => host?.on('run-start', ({ threadId: id }) => {
    runContext.current.set(id, JSON.stringify(context.read()))
  }), [host, context.read])
  useEffect(() => {
    if (receipt?.threadId === threadId) record(receipt)
    else if (observed?.threadId === threadId && observed.status === 'unknown') record(observed)
    // 回执的订阅身份稳定；普通 token 或页面切换不得重新锚定已有步骤。
  }, [receipt, observed, threadId])
  useEffect(() => {
    epoch.current += 1
    operation.current?.abort()
    operation.current = null
    lock.current = false
    setBusy(false)
    setError('')
    return (): void => { epoch.current += 1; operation.current?.abort() }
  }, [threadId, endpoint])

  useEffect(() => {
    setSteps((previous) => previous.map((step) => step.threadId === threadId && step.value.pendingInteraction
      && !step.action && step.context !== currentContext ? { ...step, action: '已失效' } : step))
  }, [currentContext, threadId, receipt])

  /** 恢复只回答原待办；同步锁阻止同一帧双击，线程切换令迟到响应失效。 */
  async function execute(step?: Step, value?: string | boolean): Promise<void> {
    if (!host || !agentId || !threadId || lock.current || running) return
    const pending = step?.value.pendingInteraction
    if (step && (!pending || step.action || step.threadId !== threadId || step.context !== JSON.stringify(context.read())
      || steps.filter((item) => item.threadId === threadId).at(-1)?.key !== step.key)) return
    const currentEpoch = ++epoch.current
    lock.current = true
    setBusy(true)
    setError('')
    /** 只更新原线程的原卡片，不更改业务目标或其他待办。 */
    const mark = (action: ActionState): void => {
      if (step) setSteps((previous) => previous.map((item) => item.key === step.key && item.threadId === step.threadId ? { ...item, action } : item))
    }
    mark('处理中')
    try {
      if (pending && value !== undefined && runtime?.interrupts.some((item) => item.id === pending.interactionId)) {
        await host.resumeInterrupts([{ interruptId: pending.interactionId, status: 'resolved', payload: value }])
      } else {
        const sourceContext = context.read()
        const request = requestRuntimeState({ endpoint, agentId, threadId, context: sourceContext,
          ...(pending && value !== undefined ? { response: { interruptId: pending.interactionId,
            status: 'resolved' as const, payload: value } } : {}) })
        operation.current = request
        const result = await request.result
        if (currentEpoch === epoch.current && thread.current === threadId) {
          // inspect 没有待办仅说明当前无待办，绝不证明未知操作成功。
          record(!pending && result.status === 'completed' ? { ...result, status: 'unknown', result: undefined,
            reason: 'terminal_result_missing' } : result, JSON.stringify(sourceContext))
        }
      }
      if (currentEpoch === epoch.current) {
        const snapshot = host.getSnapshot().runtime
        mark(snapshot?.run.status === 'error' ? '结果未知' : value === false ? '已取消' : value === true ? '已确认' : '已回答')
      }
    } catch {
      if (currentEpoch === epoch.current) {
        mark('结果未知')
        setError('未能确认执行状态，请检查执行状态。系统不会自动重发原操作。')
      }
    } finally {
      if (currentEpoch === epoch.current) { operation.current = null; lock.current = false; setBusy(false) }
    }
  }
  const visible = steps.filter((item) => item.threadId === threadId)
  return <BusinessCards.Provider value={{ notices, steps: visible, threadId, running, currentContext, error, resultSchema,
    inspect: (): void => { void execute() }, respond: (step, value): void => { void execute(step, value) } }}>{children}</BusinessCards.Provider>
}

/** 只在官方指定的消息后渲染对应步骤，不新增消息、请求或滚动容器。 */
export function RuntimeBusinessCards({ afterMessageId }: { afterMessageId: string | null }): ReactElement | null {
  const cards = useContext(BusinessCards)
  if (!cards) return null
  const latest = cards.steps.at(-1)
  return <ConversationTransientMessages afterMessageId={afterMessageId} renderMessage={(message) => {
    const step = cards.steps.find((item) => item.noticeId === message.id)
    return step ? <BusinessStep step={step} cards={cards} latest={step === latest} />
      : <p role={message.type === 'error' ? 'alert' : 'note'}>{message.text}</p>
  }} />
}

/** 确认、澄清与结果均使用可读文字；历史动作保持锁定。 */
function BusinessStep({ step, cards, latest }: { step: Step; cards: Cards; latest: boolean }): ReactElement | null {
  const [answer, setAnswer] = useState('')
  const pending = step.value.pendingInteraction
  const stale = Boolean(pending) && (!latest || step.context !== cards.currentContext)
  const locked = cards.running || Boolean(step.action) || stale
  const recovery = latest && (['unknown', 'failed'].includes(step.value.status) || stale || step.action === '结果未知' || step.action === '已失效')
  const completed = step.value.status === 'completed'
  // 普通对话的空成功回执不占位；有业务内容时仅展示结果，不重复成功提示。
  if (completed && !hasBusinessValue(step.value.result)) return null
  return <section className="shared-agent-step" aria-label={pending ? '待处理交互' : latest ? '业务结果' : '历史业务结果'}
    data-runtime-business-status={latest ? step.value.status : undefined}>
    {!completed && <p role="status">
      {pending ? pending.kind === 'clarification' ? '需要补充信息' : '请确认操作' : runtimeResultLabel(step.value)}</p>}
    {pending && <>
      <p>{pending.message}</p>
      <BusinessValue value={pending.payload} />
      <p role="status">{step.action ?? (stale ? '已失效：当前页面或业务目标已变化，请检查原待办。' : '等待处理')}</p>
      {pending.kind === 'clarification' ? <form onSubmit={(event) => { event.preventDefault(); if (!locked && answer.trim()) cards.respond(step, answer.trim()) }}>
        <label>补充信息<textarea value={answer} onChange={(event) => setAnswer(event.target.value)} disabled={locked} /></label>
        <button type="submit" disabled={locked || !answer.trim()}>提交回答</button>
      </form> : <div className="shared-agent-step-actions">
        <button type="button" disabled={locked} onClick={() => cards.respond(step, true)}>确认</button>
        <button type="button" disabled={locked} onClick={() => cards.respond(step, false)}>取消</button>
      </div>}
    </>}
    {step.value.error && <p role="alert">{step.value.error.message}</p>}
    {step.value.status === 'completed' && <BusinessValue value={step.value.result} schema={cards.resultSchema} />}
    {latest && cards.error && <p role="alert">{cards.error}</p>}
    {recovery && <><p>检查仅查询当前状态，不会重新执行原操作。</p>
      <button type="button" disabled={cards.running} onClick={cards.inspect}>检查执行状态</button></>}
  </section>
}

/** 将展示通知交给同一个官方 Runtime 绑定，不写入模型历史。 */
export function useBusinessTransientMessages(): ConversationTransientMessageService | undefined {
  return useContext(BusinessCards)?.notices
}
