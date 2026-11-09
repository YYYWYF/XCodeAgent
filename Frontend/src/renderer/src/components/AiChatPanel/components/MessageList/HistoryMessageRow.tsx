import { useEffect, useRef, useState, type ReactNode } from 'react'

type HistoryMessageRowProps = {
  id: number
  initiallyVisible: boolean
  protectedRow: boolean
  render: () => ReactNode
}

/** 先判断消息是否有展示内容，隐藏消息不创建占位容器，也不参与列表间距。 */
export function HistoryMessageRow({ render, ...props }: HistoryMessageRowProps): JSX.Element | null {
  // 这里只构造 React 元素，重卡片仍由视口组件控制挂载；空消息不能保留虚拟高度。
  const content = render()
  return content == null ? null : <HistoryMessageViewport {...props}>{content}</HistoryMessageViewport>
}

/** 只挂载视口附近的重消息卡片；交互过的表单保留挂载，防止未保存编辑丢失。 */
function HistoryMessageViewport({
  id,
  initiallyVisible,
  protectedRow,
  children
}: Omit<HistoryMessageRowProps, 'render'> & { children: ReactNode }): JSX.Element {
  const ref = useRef<HTMLDivElement>(null)
  const height = useRef(120)
  const [visible, setVisible] = useState(initiallyVisible)
  const [interacted, setInteracted] = useState(false)
  const mounted = visible || protectedRow || interacted

  useEffect(() => {
    const element = ref.current
    if (!element || typeof IntersectionObserver === 'undefined') {
      setVisible(true)
      return
    }
    const scoped = new IntersectionObserver(([entry]) => setVisible(entry.isIntersecting), {
      root: element.parentElement?.parentElement || null,
      rootMargin: '800px 0px'
    })
    scoped.observe(element)
    return () => scoped.disconnect()
  }, [])

  useEffect(() => {
    const element = ref.current
    if (!element || !mounted || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(() => {
      height.current = element.getBoundingClientRect().height
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [mounted])

  return (
    <div
      ref={ref}
      data-message-id={id}
      style={{ flexShrink: 0, ...(mounted ? {} : { height: height.current }) }}
      onFocusCapture={() => setInteracted(true)}
      onChangeCapture={() => setInteracted(true)}
    >
      {mounted ? children : null}
    </div>
  )
}
