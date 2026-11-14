import { createContext, useCallback, useContext, useEffect, useRef, type ReactElement, type ReactNode } from 'react'
import type { Context } from '@ag-ui/core'

export interface SharedAgentContext {
  actionId?: string
  contextItems?: readonly { id: string; label: string; value: string }[]
  input?: Record<string, unknown>
}

interface ContextService {
  read: () => readonly Context[]
  register: (owner: symbol, value: SharedAgentContext | null) => void
  setInput: (value: Record<string, unknown> | undefined) => void
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

/** 业务数据只经标准 Context 发送，页面路由变化立即隔离旧记录。 */
export function SharedRunContextProvider({ pageId, description, children }:
  { pageId: string; description?: string; children: ReactNode }): ReactElement {
  const page = useRef(pageId)
  const selection = useRef<{ owner: symbol; pageId: string; value: SharedAgentContext } | null>(null)
  const input = useRef<{ pageId: string; value: Record<string, unknown> | undefined } | null>(null)
  const descriptionRef = useRef(description)
  page.current = pageId
  descriptionRef.current = description
  /** 表单输入按当前页面隔离；回调身份稳定，不重建官方 Runtime。 */
  const setInput = useCallback((value: Record<string, unknown> | undefined): void => {
    input.current = { pageId: page.current, value }
  }, [])
  /** 只登记当前挂载页面的值，旧页面清理不能覆盖新页面登记。 */
  const register = useCallback((owner: symbol, value: SharedAgentContext | null): void => {
    if (value) selection.current = { owner, pageId: page.current, value }
    else if (selection.current?.owner === owner) selection.current = null
  }, [])
  /** 每次发送/恢复时读取，不把创建回调时的记录作为后续请求的快照。 */
  const read = useCallback((): readonly Context[] => {
    const currentPage = page.current
    const selected = selection.current?.pageId === currentPage ? selection.current.value : undefined
    const result: Context[] = currentPage ? [{ description: 'xcodeagent.page', value: JSON.stringify({
      pageId: currentPage, actionId: selected?.actionId ?? 'conversation', contextItems: selected?.contextItems ?? [],
    }) }] : []
    const businessInput = selected?.input ?? (input.current?.pageId === currentPage ? input.current.value : undefined)
    if (businessInput) result.push({ description: 'xcodeagent.input', value: JSON.stringify(businessInput) })
    if (descriptionRef.current) result.push({ description: 'description', value: descriptionRef.current })
    return result
  }, [])
  return <RunContext.Provider value={{ read, register, setInput }}>{children}</RunContext.Provider>
}
