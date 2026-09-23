/**
 * 设计稿运行时渲染器（方案 B 的核心组件）。
 *
 * 替代原先的 DesignPreviewFrame（远程 iframe 加载独立 Vite 工程的做法）：
 * 不再 clone 模板 / pnpm install / 起 dev server，而是用一个隔离 iframe
 * （通过专用协议加载 public/design-runtime/design-frame.html），注入预打包好的 antd5
 * runtime bundle，再用 sucrase 在浏览器端编译 .tsx 源码并动态挂载。
 *
 * 为什么用独立 HTML 文件而非 srcdoc：主窗口 index.html 的 CSP 是
 * script-src 'self'（无 'unsafe-inline'），srcdoc iframe 继承父 CSP，
 * 会导致注入的 inline script 被静默阻止。独立协议让 iframe 与主窗口分属
 * 不同 origin；design-frame.html 自带 CSP，inline script / new Function 可用。
 *
 * 通信：主窗口编译 .tsx → postMessage 把 compiled JS 发给 iframe →
 * iframe 内执行并挂载组件 → postMessage 通知主窗口。主窗口不访问 iframe DOM。
 */

import { useEffect, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import { Typography } from 'antd'
import { cx } from '../../utils'
import { compileTsx } from './compileTsx'
import './DesignRenderer.less'

const { Paragraph } = Typography
const DESIGN_FRAME_URL = 'devagentstudio-design://runtime/design-frame.html'

type RenderState =
  | { kind: 'idle' }
  | { kind: 'compiling' }
  | { kind: 'ready' }
  | { kind: 'error'; message: string }

type Props = {
  /** 设计稿 .tsx 源码字符串（LLM 生成、自包含、antd5）。 */
  code: string
  /** iframe 标题。 */
  title?: string
  /** 全屏模式：iframe 撑满视口高度。 */
  fullscreen?: boolean
}

export default function DesignRenderer({
  code,
  title,
  fullscreen = false
}: Props): ReactElement {
  const iframeRef = useRef<HTMLIFrameElement | null>(null)
  const nonceRef = useRef(crypto.randomUUID())
  const frameUrl = `${DESIGN_FRAME_URL}#${new URLSearchParams({ nonce: nonceRef.current })}`
  const [state, setState] = useState<RenderState>({ kind: 'idle' })
  // 缓存最新的 code 和 pending 的渲染请求，供 message 回调读取。
  const codeRef = useRef(code)
  codeRef.current = code
  const pendingRenderRef = useRef<((ok: boolean, payload: Record<string, unknown>) => void) | null>(null)
  const frameReadyRef = useRef(false)
  const frameErrorRef = useRef<string | null>(null)

  // 轮询握手，避免 file URL 下 iframe 比 React effect 更早发出 ready 消息。
  function waitForFrameReady(win: Window, timeoutMs = 20000): Promise<void> {
    return new Promise((resolve, reject) => {
      const start = Date.now()
      const check = (): void => {
        if (frameErrorRef.current) {
          reject(new Error(frameErrorRef.current))
          return
        }
        if (frameReadyRef.current) {
          resolve()
          return
        }
        if (Date.now() - start > timeoutMs) {
          reject(new Error('iframe 就绪超时'))
          return
        }
        win.postMessage({ type: 'design-frame-ping', nonce: nonceRef.current }, '*')
        setTimeout(check, 50)
      }
      check()
    })
  }

  useEffect(() => {
    const iframe = iframeRef.current
    if (!iframe || !code) {
      setState({ kind: 'idle' })
      return
    }
    let cancelled = false
    setState({ kind: 'compiling' })

    const onMessage = (event: MessageEvent): void => {
      if (event.source !== iframe.contentWindow) return
      const data = event.data || {}
      if (data.nonce !== nonceRef.current) return
      if (data.type === 'design-frame-ready') {
        frameReadyRef.current = true
        return
      }
      if (data.type === 'design-frame-error') {
        frameErrorRef.current = String(data.error || '设计稿运行时加载失败')
        return
      }
      if (data.type === 'design-rendered') {
        const cb = pendingRenderRef.current
        pendingRenderRef.current = null
        if (cb) cb(Boolean(data.ok), data)
      }
    }
    window.addEventListener('message', onMessage)

    const run = async (): Promise<void> => {
      try {
        const win = iframe.contentWindow
        if (!win) throw new Error('iframe contentWindow 不可用')
        await waitForFrameReady(win)
        if (cancelled) return

        // 主窗口编译 .tsx → compiled JS 字符串。
        const compiled = compileTsx(codeRef.current)

        // postMessage 发给 iframe 执行，等回执。
        const result = await new Promise<Record<string, unknown>>((resolve, reject) => {
          pendingRenderRef.current = (ok, payload) => {
            if (ok) resolve(payload)
            else reject(new Error(payload.error ? `${payload.error}${payload.stack ? '\n' + payload.stack : ''}` : '未知错误'))
          }
          win.postMessage({ type: 'design-render', compiled, nonce: nonceRef.current }, '*')
          // 超时兜底
          setTimeout(() => {
            if (pendingRenderRef.current) {
              pendingRenderRef.current = null
              reject(new Error('iframe 执行编译产物超时'))
            }
          }, 15000)
        })
        if (cancelled) return

        if (!result.ok) {
          throw new Error(
            String(result.error || '执行失败') +
            (result.stack ? `\n${String(result.stack)}` : '')
          )
        }

        if (cancelled) return
        setState({ kind: 'ready' })
      } catch (err) {
        if (cancelled) return
        const message = err instanceof Error ? err.message : String(err)
        setState({ kind: 'error', message })
      }
    }
    run()

    return () => {
      cancelled = true
      window.removeEventListener('message', onMessage)
    }
  }, [code])

  return (
    <div className={cx('design-renderer-wrap')}>
      <iframe
        className={cx('design-renderer-iframe', fullscreen && 'is-fullscreen')}
        ref={iframeRef}
        src={frameUrl}
        title={title || '设计稿预览'}
        sandbox="allow-scripts allow-same-origin"
      />
      {state.kind === 'compiling' ? (
        <div className={cx('design-renderer-loading')}>设计稿渲染中…</div>
      ) : null}
      {state.kind === 'error' ? (
        <div className={cx('design-renderer-fallback')}>
          <Paragraph type="secondary" style={{ margin: 0 }}>
            设计稿渲染失败。
          </Paragraph>
          <Paragraph
            type="secondary"
            style={{ margin: '8px 0 0', whiteSpace: 'pre-wrap' }}
          >
            {state.message}
          </Paragraph>
        </div>
      ) : null}
    </div>
  )
}
