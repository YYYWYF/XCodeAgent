/**
 * 设计稿运行时渲染器（方案 B 的核心组件）。
 *
 * 替代原先的 DesignPreviewFrame（远程 iframe 加载独立 Vite 工程的做法）：
 * 不再 clone 模板 / pnpm install / 起 dev server，而是用一个同源 iframe
 * （加载 public/design-runtime/design-frame.html），注入预打包好的 antd5
 * runtime bundle，再用 sucrase 在浏览器端编译 .tsx 源码并动态挂载。
 *
 * 为什么用独立 HTML 文件而非 srcdoc：主窗口 index.html 的 CSP 是
 * script-src 'self'（无 'unsafe-inline'），srcdoc iframe 继承父 CSP，
 * 会导致注入的 inline script 被静默阻止。design-frame.html 自带宽松 CSP，
 * 作为 iframe src 时不继承父窗口 CSP，inline script / new Function 可用。
 *
 * 通信：主窗口编译 .tsx → postMessage 把 compiled JS 发给 iframe →
 * iframe 内 new Function 执行，把组件挂到 window.__DESIGN_COMPONENT__ →
 * postMessage 通知主窗口 → 主窗口取回组件用 ReactDOM.createRoot 挂载。
 */

import { useEffect, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import { Typography } from 'antd'
import { cx } from '../../utils'
import { compileTsx } from './compileTsx'
import './DesignRenderer.less'

const { Paragraph } = Typography

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

// iframe 容器文档（自带宽松 CSP，不继承父窗口）。
const DESIGN_FRAME_URL = '/design-runtime/design-frame.html'

export default function DesignRenderer({
  code,
  title,
  fullscreen = false
}: Props): ReactElement {
  const iframeRef = useRef<HTMLIFrameElement | null>(null)
  const [state, setState] = useState<RenderState>({ kind: 'idle' })
  // 缓存最新的 code 和 pending 的渲染请求，供 message 回调读取。
  const codeRef = useRef(code)
  codeRef.current = code
  const pendingRenderRef = useRef<((ok: boolean, payload: Record<string, unknown>) => void) | null>(null)
  const frameReadyRef = useRef(false)

  // 等待 iframe 内 runtime bundle 加载就绪（window.__DESIGN_RUNTIME__ 出现）。
  function waitForRuntime(
    win: Window,
    timeoutMs = 10000
  ): Promise<void> {
    return new Promise((resolve, reject) => {
      const start = Date.now()
      const tick = () => {
        const rt = (win as unknown as Record<string, unknown>).__DESIGN_RUNTIME__
        if (rt) {
          resolve()
          return
        }
        if (Date.now() - start > timeoutMs) {
          reject(new Error('antd5 runtime bundle 加载超时'))
          return
        }
        setTimeout(tick, 50)
      }
      tick()
    })
  }

  // 等待 iframe 发回 design-frame-ready。
  function waitForFrameReady(timeoutMs = 10000): Promise<void> {
    return new Promise((resolve, reject) => {
      const start = Date.now()
      const check = () => {
        if (frameReadyRef.current) {
          resolve()
          return
        }
        if (Date.now() - start > timeoutMs) {
          reject(new Error('iframe 就绪超时'))
          return
        }
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

    const onMessage = (event: MessageEvent) => {
      if (event.source !== iframe.contentWindow) return
      const data = event.data || {}
      if (data.type === 'design-frame-ready') {
        frameReadyRef.current = true
        return
      }
      if (data.type === 'design-rendered') {
        const cb = pendingRenderRef.current
        pendingRenderRef.current = null
        if (cb) cb(Boolean(data.ok), data)
      }
    }
    window.addEventListener('message', onMessage)

    const run = async () => {
      try {
        const win = iframe.contentWindow
        if (!win) throw new Error('iframe contentWindow 不可用')
        await waitForFrameReady()
        await waitForRuntime(win)
        if (cancelled) return

        // 主窗口编译 .tsx → compiled JS 字符串。
        const compiled = compileTsx(codeRef.current)

        // postMessage 发给 iframe 执行，等回执。
        const result = await new Promise<Record<string, unknown>>((resolve, reject) => {
          pendingRenderRef.current = (ok, payload) => {
            if (ok) resolve(payload)
            else reject(new Error(payload.error ? `${payload.error}${payload.stack ? '\n' + payload.stack : ''}` : '未知错误'))
          }
          win.postMessage({ type: 'design-render', compiled }, '*')
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

        // 从 iframe 取回组件，用 iframe 内的 ReactDOM 挂载到 iframe 的 #root。
        const w = win as unknown as Record<string, unknown>
        const rt = w.__DESIGN_RUNTIME__ as Record<string, unknown>
        const ReactDOMClient = rt.ReactDOMClient as typeof import('react-dom/client')
        const React = rt.React as typeof import('react')
        const Component = w.__DESIGN_COMPONENT__
        if (!Component || typeof Component !== 'function') {
          throw new Error(
            `设计稿没有有效的默认导出组件（exports keys: ${JSON.stringify(
              result.exportKeys
            )}, component type: ${String(result.componentType)}）`
          )
        }
        const rootEl = win.document.getElementById('root')
        if (!rootEl) throw new Error('iframe 内未找到 #root 挂载点')
        const root = ReactDOMClient.createRoot(rootEl)
        root.render(React.createElement(Component as never))
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
        src={DESIGN_FRAME_URL}
        title={title || '设计稿预览'}
        sandbox="allow-same-origin allow-scripts allow-popups allow-forms"
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
