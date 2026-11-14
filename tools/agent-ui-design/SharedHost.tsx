import { memo, useCallback, useState, type CSSProperties, type ReactElement, type ReactNode } from 'react'
import type { AgentProps } from '@za38.03/agent-ui-runtime/ui'
import { Agent as ProjectAgent } from './Agent'
import { agentSurfaces } from './surfaces'
import { agentProps } from './props'
import { pagePresentation } from './presentation'
import { RuntimeBusinessPanel } from './RuntimeBusinessPanel'
import { useSharedRunContext } from './runtimeContext'
import { RuntimeInput } from './RuntimeInput'
import './shared-host.css'

const Agent = memo(ProjectAgent)
const surfaces: readonly { pageId: string; surface: string; entries: readonly { actionId: string; label: string }[] }[] = agentSurfaces

export interface SharedAgentHostProps {
  /** 来自当前正式合同；设计 Mock 不提供此身份，不能生成真实业务证据。 */
  agentId?: string
  pageId: string
  endpoint: string
  panelOpen: boolean
  onPanelOpenChange: (open: boolean) => void
  getRunContext?: AgentProps['getRunContext']
  inputSchema?: Record<string, unknown>
  standaloneBounds?: CSSProperties
  preserveLayout?: boolean
  children?: ReactNode
}

/** 在路由外常驻挂载一次；页面切换及浮窗关闭只改变容器和公开展示 props。 */
export function SharedAgentHost({ agentId, pageId, endpoint, panelOpen, onPanelOpenChange,
  children, getRunContext, inputSchema, standaloneBounds, preserveLayout = false }: SharedAgentHostProps): ReactElement {
  const page = surfaces.find((item) => item.pageId === pageId)
  const standalone = page?.surface === 'standalone_page'
  const visible = (standalone && (!preserveLayout || Boolean(standaloneBounds))) ||
    (page?.surface === 'floating_panel' && panelOpen)
  const presentation = pagePresentation[pageId]
  const [error, setError] = useState('')
  const context = useSharedRunContext()
  /** 原生发送和恢复都读取当前页面数据；允许宿主补充标准上下文。 */
  const readContext = useCallback<NonNullable<AgentProps['getRunContext']>>((input) =>
    [...context.read(), ...(getRunContext?.(input) ?? [])], [context.read, getRunContext])
  const reportError = useCallback((value: Error): void => setError(value.message), [])
  // endpoint 必须由宿主从当前连接合同提供，不能回落到包的默认 /agent。
  const connected = /^\/(?!\/)[^\s\\?#]+$/.test(endpoint)
  /** 打开已确认页面的同一个会话区。 */
  const openPanel = (): void => onPanelOpenChange(true)
  /** 关闭仅改变显示状态，不卸载 Agent。 */
  const closePanel = (): void => onPanelOpenChange(false)

  return <div className="shared-agent-host">
    <div hidden={standalone && !preserveLayout}>{children}</div>
    {presentation?.css && <style>{presentation.css}</style>}
    {page?.surface === 'floating_panel' && !panelOpen && <div className="shared-agent-launchers">
      {page.entries.map((entry) => <button key={entry.actionId} type="button"
        data-action-id={entry.actionId} onClick={openPanel} aria-expanded={false}
        aria-controls="shared-agent-conversation">{entry.label}</button>)}
    </div>}
    <section id="shared-agent-conversation" hidden={!visible} style={standalone ? standaloneBounds : undefined} data-design-page={pageId}
      className={`agent-design shared-agent-conversation ${standalone ? 'is-standalone' : 'is-floating'}`}
      aria-label="智能体会话">
      {!standalone && <button type="button" className="shared-agent-close" onClick={closePanel}
        aria-label="关闭助手">关闭助手</button>}
      {error && <p role="alert">{error}</p>}
      {connected && <RuntimeInput key={pageId} schema={inputSchema} />}
      {connected && <RuntimeBusinessPanel agentId={agentId} endpoint={endpoint} />}
      {connected ? <Agent pageId={pageId} {...agentProps} {...presentation?.props}
        endpoint={endpoint} getRunContext={readContext} onError={reportError} /> : <p role="status">等待配置智能体连接</p>}
    </section>
  </div>
}
