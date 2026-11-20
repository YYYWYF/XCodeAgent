import { createContext, useCallback, useContext, useLayoutEffect, useState,
  type CSSProperties, type ReactElement } from 'react'

export interface PageSlot { pageId: string; element: HTMLDivElement }
export const PageSlotContext = createContext<((slot: PageSlot | null) => void) | null>(null)

/** 独立页只声明布局占位，Agent 仍常驻于路由外同一 DOM 位置。 */
export function SharedAgentPage({ pageId }: { pageId: string }): ReactElement {
  const register = useContext(PageSlotContext)
  if (!register) throw new Error('SharedAgentApplication is required')
  /** 页面卸载只释放布局占位，不销毁会话。 */
  const ref = useCallback((element: HTMLDivElement | null): void => {
    register(element ? { pageId, element } : null)
  }, [pageId, register])
  return <div ref={ref} className="shared-agent-page-slot" aria-label="智能体页面" />
}

/** 跟随宿主内容区的实际尺寸，仅移动同一个容器，不改 key 或 portal 目标。 */
export function usePageBounds(slot: PageSlot | null, pageId: string): CSSProperties | undefined {
  const [bounds, setBounds] = useState<CSSProperties>()
  useLayoutEffect(() => {
    if (!slot || slot.pageId !== pageId) {
      setBounds(undefined)
      return
    }
    /** 占位只提供顶边与横向位置；绝对高度来自预览视口，不依赖业务页的百分比高度。 */
    const update = (): void => {
      const { top: anchor, left, width } = slot.element.getBoundingClientRect()
      const top = Math.max(0, anchor)
      const height = Math.max(0, (document.documentElement.clientHeight || window.innerHeight) - top)
      setBounds((previous) => previous?.top === top && previous.left === left &&
        previous.width === width && previous.height === height
        ? previous : { position: 'fixed', top, left, width, height })
    }
    update()
    const observer = new ResizeObserver(update)
    // 工具栏换行、Shell 改宽等变化也可能只移动占位，不改变占位自身的尺寸。
    for (let element: HTMLElement | null = slot.element; element; element = element.parentElement) {
      observer.observe(element)
    }
    window.addEventListener('resize', update)
    window.addEventListener('scroll', update, true)
    return () => {
      observer.disconnect()
      window.removeEventListener('resize', update)
      window.removeEventListener('scroll', update, true)
    }
  }, [slot, pageId])
  return slot?.pageId === pageId ? bounds : undefined
}
