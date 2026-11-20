import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useRef, useState, type ReactElement, type ReactNode } from 'react'
import type { Context } from '@ag-ui/core'

export interface SharedAgentContext {
  actionId?: string
  contextItems?: readonly { id: string; label: string; value: string }[]
  input?: Record<string, unknown>
}

interface ContextService {
  observation: SharedContextObservation | null
  read: () => readonly Context[]
  register: (owner: symbol, value: SharedAgentContext | null) => void
  setInput: (value: Record<string, unknown> | undefined) => void
}

export interface SharedContextObservation {
  pageId: string
  actionId: string
  fields: readonly { name: string; source: 'page_record' | 'page_input' | 'form_input' }[]
  truncated: boolean
  hasDescription: boolean
  revision: number
  updatedAt: number
}

/** 观察元数据只允许有界标识；描述、标签、记录值和输入值均不进入摘要。 */
function observationName(value: string): string {
  return /^[a-zA-Z0-9_./-]{1,128}$/.test(value) ? value : ''
}

const RunContext = createContext<ContextService | null>(null)

/** 页面声明当前选择和业务输入；卸载只释放自己登记的数据。 */
export function useSharedAgentContext(value: SharedAgentContext): void {
  const service = useContext(RunContext)
  const owner = useRef(Symbol('page-context'))
  const encoded = JSON.stringify(value)
  useEffect(() => {
    if (!service) throw new Error('SharedAgentApplication is required')
    service.register(owner.current, JSON.parse(encoded) as SharedAgentContext)
    return () => service.register(owner.current, null)
  }, [service?.register, encoded])
}

/** Host 和查询共用同一份最新上下文，不改变官方会话或消息所有权。 */
export function useSharedRunContext(): ContextService {
  const value = useContext(RunContext)
  if (!value) throw new Error('SharedAgentApplication is required')
  return value
}

/** 仅订阅当前页面的脱敏元数据，不调用 read、不发送请求或保留原始业务值。 */
export function useSharedContextObservation(): SharedContextObservation | null {
  return useContext(RunContext)?.observation ?? null
}

/** 业务数据只经标准 Context 发送，页面路由变化立即隔离旧记录。 */
export function SharedRunContextProvider({ pageId, description, children }:
  { pageId: string; description?: string; children: ReactNode }): ReactElement {
  const page = useRef(pageId)
  const selection = useRef<{ owner: symbol; pageId: string; value: SharedAgentContext } | null>(null)
  const input = useRef<{ pageId: string; value: Record<string, unknown> | undefined } | null>(null)
  const descriptionRef = useRef(description)
  const revision = useRef(0)
  const [observation, setObservation] = useState<SharedContextObservation | null>(null)
  page.current = pageId
  descriptionRef.current = description
  /** 更新只读摘要；同名字段的记录值改变也推进修订号，不复制该值。 */
  const refreshObservation = useCallback((): void => {
    const selected = selection.current?.pageId === page.current ? selection.current.value : undefined
    const businessInput = selected?.input ?? (input.current?.pageId === page.current ? input.current.value : undefined)
    const fields: SharedContextObservation['fields'][number][] = []
    const records = selected?.contextItems ?? []
    for (const item of records.slice(0, 32)) {
      fields.push({ name: observationName(item.id) || '[隐藏字段名]', source: 'page_record' })
    }
    const names = Object.keys(businessInput ?? {})
    for (const name of names.slice(0, 32 - fields.length)) {
      fields.push({ name: observationName(name) || '[隐藏字段名]', source: selected?.input ? 'page_input' : 'form_input' })
    }
    revision.current += 1
    setObservation({ pageId: observationName(page.current), actionId: observationName(selected?.actionId ?? 'conversation'),
      fields, truncated: records.length + names.length > 32, hasDescription: Boolean(descriptionRef.current),
      revision: revision.current, updatedAt: Date.now() })
  }, [])
  // 页面往返不能恢复旧表单参数；页面记录和服务端待办仍由各自原 owner 持有。
  useLayoutEffect(() => { input.current = null; refreshObservation() }, [pageId, refreshObservation])
  useLayoutEffect(refreshObservation, [pageId, description, refreshObservation])
  /** 表单输入按当前页面隔离；回调身份稳定，不重建官方 Runtime。 */
  const setInput = useCallback((value: Record<string, unknown> | undefined): void => {
    input.current = { pageId: page.current, value }
    refreshObservation()
  }, [refreshObservation])
  /** 只登记当前挂载页面的值，旧页面清理不能覆盖新页面登记。 */
  const register = useCallback((owner: symbol, value: SharedAgentContext | null): void => {
    if (value) selection.current = { owner, pageId: page.current, value }
    else if (selection.current?.owner === owner) selection.current = null
    else return
    refreshObservation()
  }, [refreshObservation])
  /** 每次发送/恢复时读取，不把创建回调时的记录作为后续请求的快照。 */
  const read = useCallback((): readonly Context[] => {
    const currentPage = page.current
    const selected = selection.current?.pageId === currentPage ? selection.current.value : undefined
    const result: Context[] = currentPage ? [{ description: 'xcodeagent.page', value: JSON.stringify({
      pageId: currentPage, actionId: selected?.actionId ?? 'conversation', contextItems: selected?.contextItems ?? [],
    }) }] : []
    const businessInput = selected?.input ?? (input.current?.pageId === currentPage ? input.current.value : undefined)
    if (businessInput && Object.keys(businessInput).length) result.push({ description: 'xcodeagent.input', value: JSON.stringify(businessInput) })
    if (descriptionRef.current) result.push({ description: 'description', value: descriptionRef.current })
    return result
  }, [])
  return <RunContext.Provider value={{ read, register, setInput,
    observation: observation?.pageId === observationName(pageId) ? observation : null }}>{children}</RunContext.Provider>
}
