import { useEffect, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import { Typography } from 'antd'
import { cx } from '../../utils'
import './UiDesignConfirmationPanel.less'

const { Paragraph } = Typography

type Props = {
  /** 设计稿工程 dev server 地址，如 http://localhost:3003（由后端启动后返回）。 */
  origin?: string
  /** 页面在设计稿工程里的路由路径，如 /page/order-list。 */
  routePath?: string
  /** iframe 标题。 */
  title?: string
}

// 嵌入设计稿工程 dev server 的实时渲染画面。
// - origin 为空（工程未启动）时显示提示。
// - routePath 为空或页面未就绪时显示占位文案。
// - iframe 固定高度，内容超出时在 iframe 内部滚动。
// - 先用 fetch 探测端口可达性：工程未启动时 fetch 会 reject，直接显示提示，
//   避免跨域 iframe 加载错误页时 onLoad 仍触发、无法区分成败的问题。
// - 探测可达后再挂载 iframe，并保留一个加载超时兜底。
export default function DesignPreviewFrame({
  origin,
  routePath,
  title
}: Props): ReactElement {
  // reachable: null=探测中, true=可达, false=不可达
  const [reachable, setReachable] = useState<boolean | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [timedOut, setTimedOut] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const hasOrigin = Boolean(origin)
  const hasRoute = Boolean(routePath)

  useEffect(() => {
    setReachable(null)
    setLoaded(false)
    setTimedOut(false)
    if (timerRef.current) clearTimeout(timerRef.current)
    if (!hasOrigin || !hasRoute) return

    let cancelled = false
    // no-cors 模式下 fetch 成功 resolve 即代表端口可达（即便读不到 body）；
    // 工程未启动时连接被拒绝，fetch reject。
    fetch(`${origin}/?probe=1`, { mode: 'no-cors' })
      .then(() => {
        if (cancelled) return
        setReachable(true)
        // 可达后挂载 iframe，设加载超时兜底（Vite 冷启动 + antd 全量加载较慢）。
        timerRef.current = setTimeout(() => {
          if (!cancelled) setTimedOut(true)
        }, 15000)
      })
      .catch(() => {
        if (!cancelled) setReachable(false)
      })

    return () => {
      cancelled = true
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [origin, routePath, hasOrigin, hasRoute])

  // 工程未启动（后端未返回 origin）：显示提示。
  if (!hasOrigin) {
    return (
      <div className={cx('ui-design-frame-fallback')}>
        <Paragraph type="secondary" style={{ margin: 0 }}>
          设计稿预览服务未就绪。
        </Paragraph>
        <Paragraph type="secondary" style={{ margin: '8px 0 0' }}>
          后端正在准备设计稿工程，请稍候；若长时间未就绪，请检查后端日志。
        </Paragraph>
      </div>
    )
  }

  if (!hasRoute) {
    return (
      <Paragraph type="secondary">设计稿生成中或路由信息缺失。</Paragraph>
    )
  }

  const src = `${origin}${routePath}?bare=1`

  // 工程未启动：显示启动提示。
  if (reachable === false) {
    return (
      <div className={cx('ui-design-frame-fallback')}>
        <Paragraph type="secondary" style={{ margin: 0 }}>
          设计稿预览服务不可达（{origin}）。
        </Paragraph>
        <Paragraph type="secondary" style={{ margin: '8px 0 0' }}>
          请确认后端已启动设计稿工程的 dev server，或稍后重试。
        </Paragraph>
      </div>
    )
  }

  // 探测中或可达但 iframe 加载超时：显示提示。
  const showFallback = timedOut
  return (
    <div className={cx('ui-design-frame-wrap')}>
      {showFallback ? (
        <div className={cx('ui-design-frame-fallback')}>
          <Paragraph type="secondary" style={{ margin: 0 }}>
            设计稿加载超时（{src}）。
          </Paragraph>
          <Paragraph type="secondary" style={{ margin: '8px 0 0' }}>
            请确认该页面已生成且路由正确，或稍后重试。
          </Paragraph>
        </div>
      ) : null}
      {reachable ? (
        <iframe
          className={cx('ui-design-iframe')}
          onLoad={() => {
            setLoaded(true)
            if (timerRef.current) clearTimeout(timerRef.current)
          }}
          sandbox="allow-same-origin allow-scripts allow-popups allow-forms"
          src={src}
          style={{ display: showFallback ? 'none' : 'block' }}
          title={title || '设计稿预览'}
        />
      ) : null}
      {reachable && !loaded && !showFallback ? (
        <div className={cx('ui-design-frame-loading')}>设计稿加载中…</div>
      ) : null}
    </div>
  )
}
