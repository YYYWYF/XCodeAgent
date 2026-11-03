import { useCallback, useEffect, useLayoutEffect, useRef, type RefObject } from 'react'

// Electron 使用布局提交钩子；纯服务端展示测试不执行 DOM 锚点操作。
const useScrollLayoutEffect = typeof document === 'undefined' ? useEffect : useLayoutEffect

/** 加载前记录正在阅读的消息位置，前插历史后保持同一条消息留在原处。 */
export function useHistoryScroll(
  containerRef: RefObject<HTMLDivElement>,
  firstId: number | undefined,
  following: { current: boolean }
): () => void {
  const anchor = useRef<{ id: string; top: number; firstId?: number }>()
  const adjustedScrollTop = useRef<number>()

  /** 记录第一条与视口相交的消息外壳，虚拟行挂卸载时身份保持稳定。 */
  const remember = useCallback((): void => {
    const container = containerRef.current
    if (!container || following.current) return
    const top = container.getBoundingClientRect().top
    const element = [...container.querySelectorAll<HTMLElement>('[data-message-id]')].find(
      (item) => item.getBoundingClientRect().bottom > top
    )
    if (element)
      anchor.current = {
        id: element.dataset.messageId!,
        top: element.getBoundingClientRect().top,
        firstId
      }
  }, [containerRef, firstId, following])

  /** 同时补偿前插历史和虚拟行实际高度变化，避免估算占位高度使阅读位置漂移。 */
  const restore = useCallback((): void => {
    const current = anchor.current
    const container = containerRef.current
    if (!current || !container || following.current) return
    const element = container.querySelector<HTMLElement>(`[data-message-id="${current.id}"]`)
    if (!element) {
      remember()
      return
    }
    const delta = element.getBoundingClientRect().top - current.top
    if (Math.abs(delta) > 0.5) {
      adjustedScrollTop.current = container.scrollTop + delta
      container.scrollTop = adjustedScrollTop.current
    }
    current.firstId = firstId
  }, [containerRef, firstId, following, remember])

  useScrollLayoutEffect(() => {
    const current = anchor.current
    if (current && current.firstId !== firstId) restore()
  }, [firstId, restore])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    /** 自己补偿产生的 scroll 不能改选锚点；用户真正滚动时再更新阅读位置。 */
    const onScroll = (): void => {
      const adjusted = adjustedScrollTop.current
      adjustedScrollTop.current = undefined
      if (adjusted !== undefined && Math.abs(container.scrollTop - adjusted) < 1) return
      remember()
    }
    container.addEventListener('scroll', onScroll, { passive: true })
    const observer = typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(restore)
    if (container.firstElementChild) observer?.observe(container.firstElementChild)
    container.querySelectorAll('[data-message-id]').forEach((element) => observer?.observe(element))
    return () => {
      container.removeEventListener('scroll', onScroll)
      observer?.disconnect()
    }
  }, [firstId, containerRef, remember, restore])

  return remember
}
