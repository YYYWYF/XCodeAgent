import { ExportOutlined, LinkOutlined, ReloadOutlined } from '@ant-design/icons'
import { Button, Empty, Input, Result } from 'antd'
import type { InputRef } from 'antd'
import { useEffect, useRef, useState } from 'react'
import './BrowserPreview.less'

type BrowserStatus = 'idle' | 'loading' | 'ready' | 'error'

const PREVIEW_CONNECT_TIMEOUT_MS = 5000
const PREVIEW_LOAD_TIMEOUT_MS = 8000
const LOCAL_PREVIEW_HOSTS = new Set(['localhost', '127.0.0.1', '::1', '[::1]'])
const SUPPORTED_PROTOCOLS = new Set(['http:', 'https:'])

const normalizeUrl = (value: string): string => {
  const trimmedValue = value.trim()

  if (!trimmedValue) {
    return ''
  }

  return /^https?:\/\//i.test(trimmedValue) ? trimmedValue : `http://${trimmedValue}`
}

const getRawHostname = (url: string): string => {
  const urlWithoutProtocol = url.replace(/^https?:\/\//i, '')
  const authority = urlWithoutProtocol.split(/[/?#]/, 1)[0] ?? ''
  const host = authority.includes('@') ? (authority.split('@').pop() ?? '') : authority

  if (host.startsWith('[')) {
    return host.slice(1, host.indexOf(']'))
  }

  return host.split(':')[0] ?? ''
}

const isPureNumberHostname = (hostname: string): boolean => /^\d+$/.test(hostname)

const getValidPreviewUrl = (value: string): string => {
  const normalizedUrl = normalizeUrl(value)

  if (!normalizedUrl) {
    return ''
  }

  try {
    const rawHostname = getRawHostname(normalizedUrl)
    const parsedUrl = new URL(normalizedUrl)

    if (!SUPPORTED_PROTOCOLS.has(parsedUrl.protocol) || !rawHostname) {
      return ''
    }

    if (isPureNumberHostname(rawHostname)) {
      return ''
    }

    return parsedUrl.href
  } catch {
    return ''
  }
}

const isLocalPreviewUrl = (url: string): boolean => {
  try {
    const parsedUrl = new URL(url)

    return LOCAL_PREVIEW_HOSTS.has(parsedUrl.hostname)
  } catch {
    return false
  }
}

const checkLocalUrlReachable = async (url: string): Promise<boolean> => {
  const controller = new AbortController()
  const timeoutId = window.setTimeout(() => controller.abort(), PREVIEW_CONNECT_TIMEOUT_MS)

  try {
    await fetch(url, {
      cache: 'no-store',
      mode: 'no-cors',
      signal: controller.signal
    })

    return true
  } catch {
    return false
  } finally {
    window.clearTimeout(timeoutId)
  }
}

function BrowserPreview(): React.JSX.Element {
  const inputRef = useRef<InputRef>(null)
  const requestIdRef = useRef(0)
  const loadTimeoutRef = useRef<number | null>(null)
  const [urlDraft, setUrlDraft] = useState('')
  const [activeUrl, setActiveUrl] = useState('')
  const [iframeVersion, setIframeVersion] = useState(0)
  const [status, setStatus] = useState<BrowserStatus>('idle')

  const clearLoadTimeout = (): void => {
    if (loadTimeoutRef.current !== null) {
      window.clearTimeout(loadTimeoutRef.current)
      loadTimeoutRef.current = null
    }
  }

  useEffect(() => clearLoadTimeout, [])

  const openUrl = async (nextValue = urlDraft): Promise<void> => {
    const nextUrl = getValidPreviewUrl(nextValue)

    if (!nextValue.trim()) {
      clearLoadTimeout()
      setActiveUrl('')
      setStatus('idle')
      return
    }

    if (!nextUrl) {
      clearLoadTimeout()
      setActiveUrl('')
      setStatus('error')
      return
    }

    const requestId = requestIdRef.current + 1
    requestIdRef.current = requestId
    clearLoadTimeout()
    setStatus('loading')
    setUrlDraft(nextUrl)

    if (isLocalPreviewUrl(nextUrl)) {
      const reachable = await checkLocalUrlReachable(nextUrl)

      if (requestIdRef.current !== requestId) {
        return
      }

      if (!reachable) {
        setActiveUrl('')
        setStatus('error')
        return
      }
    }

    setActiveUrl(nextUrl)
    setIframeVersion((currentVersion) => currentVersion + 1)

    loadTimeoutRef.current = window.setTimeout(() => {
      if (requestIdRef.current === requestId) {
        setActiveUrl('')
        setStatus('error')
      }
    }, PREVIEW_LOAD_TIMEOUT_MS)
  }

  const handleIframeLoad = (): void => {
    clearLoadTimeout()
    setStatus('ready')
  }

  const handleIframeError = (): void => {
    clearLoadTimeout()
    setActiveUrl('')
    setStatus('error')
  }

  const reload = (): void => {
    void openUrl(activeUrl || urlDraft)
  }

  return (
    <section className="browser-preview">
      <header className="browser-preview__toolbar">
        <Button icon={<ReloadOutlined />} type="text" onClick={reload} />
        <Input
          ref={inputRef}
          prefix={<LinkOutlined />}
          placeholder="请输入网址，例如 localhost:3000"
          value={urlDraft}
          onChange={(event) => setUrlDraft(event.target.value)}
          onPressEnter={() => void openUrl()}
        />
        <Button icon={<ExportOutlined />} type="text" onClick={() => inputRef.current?.focus()} />
      </header>

      <div className="browser-preview__body">
        {status === 'idle' ? (
          <Empty description="请输入网址" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : null}
        {status === 'loading' ? (
          <Empty description="正在检查网址..." image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : null}
        {status === 'error' ? (
          <Result
            status="warning"
            subTitle="请检查服务是否启动，或确认网址是否允许被 iframe 加载。"
            title="网址暂时无法访问"
          />
        ) : null}
        {activeUrl ? (
          <iframe
            className={
              status === 'loading'
                ? 'browser-preview__iframe browser-preview__iframe--loading'
                : 'browser-preview__iframe'
            }
            key={`${activeUrl}-${iframeVersion}`}
            src={activeUrl}
            title="页面预览"
            onError={handleIframeError}
            onLoad={handleIframeLoad}
          />
        ) : null}
      </div>
    </section>
  )
}

export default BrowserPreview
