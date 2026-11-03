import { useEffect, useRef, useState, type ReactNode } from 'react'

/** 只挂载视口附近的重消息卡片；交互过的表单保留挂载，防止未保存编辑丢失。 */
export function HistoryMessageRow({
  id,
  initiallyVisible,
  protectedRow,
  render
}: {
  id: number
  initiallyVisible: boolean
  protectedRow: boolean
  render: () => ReactNode
}): JSX.Element {
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
      {mounted ? render() : null}
    </div>
  )
}
