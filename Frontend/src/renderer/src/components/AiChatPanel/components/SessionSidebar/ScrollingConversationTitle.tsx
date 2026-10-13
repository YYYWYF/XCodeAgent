import type { CSSProperties, ReactElement } from 'react'
import { useLayoutEffect, useRef, useState } from 'react'
import { cx } from '../../../../utils'

/** 按实际溢出距离滚动长标题，短标题保持静止，宽度变化后重新测量。 */
export default function ScrollingConversationTitle({ text }: { text: string }): ReactElement {
  const viewportRef = useRef<HTMLSpanElement>(null)
  const textRef = useRef<HTMLSpanElement>(null)
  const [distance, setDistance] = useState(0)

  useLayoutEffect(() => {
    const viewport = viewportRef.current
    const content = textRef.current
    if (!viewport || !content) return

    /** 根据可见区域计算移动距离，确保完整标题末尾能停留在视野中。 */
    const measure = (): void => {
      setDistance(Math.max(0, content.scrollWidth - viewport.clientWidth))
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(viewport)
    observer.observe(content)
    return () => observer.disconnect()
  }, [text])

  return (
    <span
      className={cx('free-chat-history-title', distance > 0 && 'scrollable')}
      ref={viewportRef}
      style={
        {
          '--conversation-title-travel': `${-distance}px`,
          '--conversation-title-duration': `${Math.max(1.8, distance / 100 + 0.6)}s`
        } as CSSProperties
      }
    >
      <span className={cx('conversation-title-text')} ref={textRef}>
        {text}
      </span>
    </span>
  )
}
