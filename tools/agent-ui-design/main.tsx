import { createRoot } from 'react-dom/client'
import { useEffect, useState, type ReactElement } from 'react'
import { App } from './App'
import { designPages } from './design/pages'
import './styles.css'

/** 只读取受控预览 HTTP 文档注入的临时连接；磁盘构建没有默认 Mock 或真实地址。 */
function readPreview(): Parameters<typeof App>[0] {
  const meta = document.querySelector<HTMLMetaElement>('meta[name="agent-ui-design-preview"]')
  if (!meta) return {}
  try {
    const value: unknown = JSON.parse(meta.content)
    if (!value || typeof value !== 'object' || !('source' in value) || value.source !== 'official-mock' ||
        !('endpoint' in value) || typeof value.endpoint !== 'string' ||
        !/^\/[a-f0-9]{32}\/agent$/.test(value.endpoint) || !('pageId' in value)) return {}
    const page = designPages.find((item) => item.pageId === value.pageId)
    return page ? { pageId: page.pageId, endpoint: value.endpoint, source: 'official-mock' } : {}
  } catch {
    return {}
  }
}

const root = document.getElementById('root')
if (!root) throw new Error('Missing design root')

/** 单向报告当前文档身份；消息不能请求主窗口执行文件、网络或 IPC 操作。 */
function report(type: 'agent-ui-design-ready' | 'agent-ui-design-error', pageId: string | undefined): void {
  const meta = document.querySelector<HTMLMetaElement>('meta[name="agent-ui-design-preview"]')
  if (!meta || window.parent === window) return
  try {
    const value = JSON.parse(meta.content) as Record<string, unknown>
    if (!/^[a-f0-9]{32}$/.test(String(value.launchId)) || !/^[a-f0-9]{64}$/.test(String(value.revision))) return
    window.parent.postMessage({ type, launchId: value.launchId, revision: value.revision, pageId }, '*')
  } catch {
    // 缺少有效平台注入时不向外报告；目标源码不保存工具位置或端口。
  }
}

/** hash 只选择已生成页面；同一根树和 Agent 在切页期间始终保留。 */
function PreviewRoot(): ReactElement {
  const [hash, setHash] = useState(window.location.hash)
  const config = readPreview()
  const selected = new URLSearchParams(hash.slice(1)).get('pageId')
  const pageId = selected === null ? config.pageId : selected
  useEffect(() => {
    /** 只读取本 iframe 的 URL，不监听父窗口的命令消息。 */
    const select = (): void => setHash(window.location.hash)
    window.addEventListener('hashchange', select)
    return () => window.removeEventListener('hashchange', select)
  }, [])
  useEffect(() => {
    report(designPages.some((page) => page.pageId === pageId) ? 'agent-ui-design-ready' : 'agent-ui-design-error', pageId)
    /** 运行异常只报告当前身份，不把堆栈或不可信内容传给宿主执行。 */
    const failed = (): void => report('agent-ui-design-error', pageId)
    window.addEventListener('error', failed)
    window.addEventListener('unhandledrejection', failed)
    return () => {
      window.removeEventListener('error', failed)
      window.removeEventListener('unhandledrejection', failed)
    }
  }, [pageId])
  /** 页面控件只选择正式清单中的预览页面，不改变父窗口或访问网络。 */
  const navigate = (targetPageId: string): void => {
    if (designPages.some((page) => page.pageId === targetPageId)) {
      window.location.hash = new URLSearchParams({ pageId: targetPageId }).toString()
    }
  }
  return <App {...config} pageId={pageId} onNavigatePage={navigate} />
}

createRoot(root).render(<PreviewRoot />)
