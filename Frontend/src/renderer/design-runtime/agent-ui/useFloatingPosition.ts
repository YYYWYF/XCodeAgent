import { useCallback, useEffect, useRef, useState } from 'react'
import { clampAndSnapFloatingPosition } from './contracts'
import type { FloatingPoint } from './types'

const LAUNCHER_SIZE = 56
const VIEWPORT_MARGIN = 16

/** 判断当前视口是否使用移动端固定入口。 */
function isCompactViewport(): boolean {
  return typeof window !== 'undefined' && window.matchMedia('(max-width: 767px)').matches
}

/** 计算浮动入口默认位于右下安全区的位置。 */
function defaultPosition(): FloatingPoint {
  if (typeof window === 'undefined') return { x: VIEWPORT_MARGIN, y: VIEWPORT_MARGIN }
  return {
    x: Math.max(VIEWPORT_MARGIN, window.innerWidth - LAUNCHER_SIZE - VIEWPORT_MARGIN),
    y: Math.max(VIEWPORT_MARGIN, window.innerHeight - LAUNCHER_SIZE - 32)
  }
}

/** 管理桌面浮动入口的拖动、视口夹紧和边缘吸附。 */
export function useFloatingPosition(): {
  position: FloatingPoint
  compact: boolean
  dragHandlers: React.HTMLAttributes<HTMLElement>
  consumeClickSuppressed: () => boolean
} {
  const [position, setPosition] = useState<FloatingPoint>(defaultPosition)
  const [compact, setCompact] = useState(isCompactViewport)
  const dragStart = useRef<{ pointer: FloatingPoint; position: FloatingPoint } | null>(null)
  const moved = useRef(false)

  /** 将任意位置重新限制到当前视口。 */
  const clampToViewport = useCallback((point: FloatingPoint): FloatingPoint => {
    return clampAndSnapFloatingPosition(
      point,
      { width: window.innerWidth, height: window.innerHeight },
      { width: LAUNCHER_SIZE, height: LAUNCHER_SIZE },
      VIEWPORT_MARGIN
    )
  }, [])

  useEffect(() => {
    /** 在视口尺寸变化后更新断点并重新夹紧入口。 */
    function handleResize(): void {
      setCompact(isCompactViewport())
      setPosition((current) => clampToViewport(current))
    }
    handleResize()
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [clampToViewport])

  /** 记录桌面拖动起点，移动端保留页面滚动手势。 */
  function handlePointerDown(event: React.PointerEvent<HTMLElement>): void {
    if (compact) return
    event.currentTarget.setPointerCapture(event.pointerId)
    dragStart.current = {
      pointer: { x: event.clientX, y: event.clientY },
      position
    }
    moved.current = false
  }

  /** 在拖动过程中实时夹紧入口，避免离开可视区域。 */
  function handlePointerMove(event: React.PointerEvent<HTMLElement>): void {
    if (!dragStart.current || compact) return
    const deltaX = event.clientX - dragStart.current.pointer.x
    const deltaY = event.clientY - dragStart.current.pointer.y
    moved.current = moved.current || Math.abs(deltaX) + Math.abs(deltaY) > 4
    setPosition(
      clampToViewport({
        x: dragStart.current.position.x + deltaX,
        y: dragStart.current.position.y + deltaY
      })
    )
  }

  /** 结束拖动并保留一次点击抑制标记，避免拖动误开面板。 */
  function handlePointerUp(event: React.PointerEvent<HTMLElement>): void {
    if (!dragStart.current || compact) return
    event.currentTarget.releasePointerCapture(event.pointerId)
    dragStart.current = null
    setPosition((current) => clampToViewport(current))
  }

  /** 消费拖动后的点击抑制状态。 */
  function consumeClickSuppressed(): boolean {
    const suppressed = moved.current
    moved.current = false
    return suppressed
  }

  return {
    position,
    compact,
    dragHandlers: {
      onPointerDown: handlePointerDown,
      onPointerMove: handlePointerMove,
      onPointerUp: handlePointerUp
    },
    consumeClickSuppressed
  }
}
