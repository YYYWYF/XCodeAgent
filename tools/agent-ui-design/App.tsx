import { memo, useCallback, useState, type ReactElement } from 'react'
import { Agent as ProjectAgent } from './design/Agent'
import { designPages } from './design/pages'
import { agentProps } from './design/props'
import { pagePresentation } from './design/presentation'
import labels from './design/locales.json'
import './design/host.css'

const Agent = memo(ProjectAgent)

/** 由设计画布选择真实页面；同一 Agent 始终在同一树位置，开关浮窗不卸载会话。 */
export function App({ pageId, endpoint, source, onNavigatePage }: {
  pageId?: string; endpoint?: string; source?: 'official-mock'; onNavigatePage?: (pageId: string) => void
}): ReactElement {
  const page = pageId === undefined ? designPages[0] : designPages.find((item) => item.pageId === pageId)
  const [openPage, setOpenPage] = useState('')
  const [error, setError] = useState('')
  const reportError = useCallback((value: Error): void => setError(value.message), [])
  if (!page) return <main role="alert">{labels.pageUnavailable}</main>
  const standalone = page.surface === 'standalone_page'
  const visible = standalone || (page.surface === 'floating_panel' && openPage === page.pageId)
  // Mock 或真实连接须由对应阶段显式提供；缺少连接时不采用包默认的 /agent。
  const connected = source === 'official-mock' && typeof endpoint === 'string' && /^\/(?!\/)[^\s\\?#]+$/.test(endpoint)
  const Page = page.component
  const presentation = pagePresentation[page.pageId]
  /** 业务入口和默认入口共用平台可见性状态，不创建第二个聊天实例。 */
  const openPanel = (): void => setOpenPage(page.pageId)
  /** 只隐藏会话面板，保留官方 Agent 及其当前线程。 */
  const closePanel = (): void => setOpenPage('')

  return (
    <main className="agent-design" data-design-page={page.pageId} data-design-surface={page.surface}>
      {presentation?.css && <style>{presentation.css}</style>}
      {error && <p role="alert">{error}</p>}
      {Page && <section className="agent-design-business" data-page-id={page.pageId}>
        <Page onOpenAgentPanel={openPanel} onCloseAgentPanel={closePanel} agentPanelOpen={visible}
          onNavigatePage={onNavigatePage ?? ignoreNavigation} />
      </section>}
      {page.surface === 'floating_panel' && !visible && !page.hasInlineLauncher && <div className="agent-design-launchers shared-agent-launchers">{page.entries.map((entry) => (
        <button key={entry.actionId} type="button" className="agent-design-launcher shared-agent-launcher"
          aria-label={entry.label} title={entry.label}
          data-action-id={entry.actionId} aria-expanded={visible}
          aria-controls="agent-design-conversation" onClick={openPanel}>
          <svg viewBox="0 0 24 24" width="24" height="24" fill="none" stroke="currentColor"
            strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
            <rect x="4" y="6" width="16" height="14" rx="4" />
            <path d="M12 3v3M9 3h6M8 12v2M16 12v2M9 17h6M2 10v6M22 10v6" />
          </svg>
        </button>
      ))}</div>}
      <section id="agent-design-conversation" hidden={!visible}
        className={`agent-design-conversation shared-agent-conversation ${standalone ? 'is-standalone' : 'is-floating'}`}
        aria-label={labels.conversation}>
        {!standalone && <header className="shared-agent-panel-header">
          <span>智能助手</span>
          <button type="button" className="agent-design-close shared-agent-close"
            aria-label={labels.close} title={labels.close} onClick={closePanel}>
            <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor"
              strokeWidth="1.75" strokeLinecap="round" aria-hidden="true" focusable="false">
              <path d="m6 6 12 12M18 6 6 18" />
            </svg>
          </button>
        </header>}
        {connected
          ? <Agent pageId={page.pageId} {...agentProps} {...presentation?.props} endpoint={endpoint} onError={reportError} />
          : <p role="status">{labels.connectionPending}</p>}
      </section>
    </main>
  )
}

/** 非交互测试或嵌入宿主未提供导航时，不自行访问外部路由。 */
function ignoreNavigation(): void {}
