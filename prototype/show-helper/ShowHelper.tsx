import { ClearOutlined } from '@ant-design/icons'
import { message, Modal } from 'antd'
import { useEffect, useRef, useState } from 'react'
import { cx } from '@renderer/utils'
import { clearPrototypeCache } from './prototypeReset'
import './ShowHelper.less'

/** 悬浮球直径（px）：拖拽边界与持久化位置校验共用。 */
const DOT_SIZE = 22
/** 悬浮球与视口边缘的最小间距（px）。 */
const EDGE_PADDING = 8
/** 指针位移小于该阈值视为点击（展开/收起菜单），超过则判定为拖拽。 */
const CLICK_SLOP = 5
/** 悬浮球位置的持久化键：随原型缓存前缀管理，“清理演示数据”后位置一并重置。 */
const POSITION_KEY = 'aistudio:prototype:show-helper-position'

/** 悬浮球在视口中的位置（fixed 定位的 left/top）。 */
type HelperPosition = { x: number; y: number }

/** 把位置限制在当前视口内，避免窗口缩小后悬浮球跑到屏幕外。 */
function clampPosition(position: HelperPosition): HelperPosition {
  const maxX = Math.max(EDGE_PADDING, window.innerWidth - DOT_SIZE - EDGE_PADDING)
  const maxY = Math.max(EDGE_PADDING, window.innerHeight - DOT_SIZE - EDGE_PADDING)
  return {
    x: Math.min(Math.max(position.x, EDGE_PADDING), maxX),
    y: Math.min(Math.max(position.y, EDGE_PADDING), maxY)
  }
}

/** 读取持久化位置；缺失、损坏或越界时回落到视口右下角。 */
function loadPosition(): HelperPosition {
  const fallback = clampPosition({
    x: window.innerWidth - DOT_SIZE - 28,
    y: window.innerHeight - DOT_SIZE - 96
  })
  try {
    const raw = window.localStorage.getItem(POSITION_KEY)
    if (!raw) return fallback
    const parsed = JSON.parse(raw) as Partial<HelperPosition>
    if (typeof parsed.x !== 'number' || typeof parsed.y !== 'number') return fallback
    return clampPosition({ x: parsed.x, y: parsed.y })
  } catch {
    return fallback
  }
}

/**
 * 原型演示辅助悬浮球：一颗常驻界面、可拖拽挪位的原点，点击展开演示辅助功能菜单。
 * 属于原型辅助能力，不进入任何业务工作流；当前只提供“清理演示数据”一个功能。
 */
export default function ShowHelper(): JSX.Element {
  const [position, setPosition] = useState<HelperPosition>(loadPosition)
  const [expanded, setExpanded] = useState(false)
  const rootRef = useRef<HTMLDivElement | null>(null)
  const positionRef = useRef<HelperPosition>(position)
  const dragRef = useRef<{
    pointerX: number
    pointerY: number
    baseX: number
    baseY: number
    moved: boolean
  } | null>(null)

  // 渲染后同步最新位置，拖拽结束时用它持久化，避免闭包读到旧状态。
  useEffect(() => {
    positionRef.current = position
  }, [position])

  // 菜单展开时点击悬浮球以外区域自动收起，避免辅助面板常驻遮挡演示内容。
  useEffect(() => {
    if (!expanded) return undefined
    const handlePointerDown = (event: PointerEvent): void => {
      if (rootRef.current && event.target instanceof Node && rootRef.current.contains(event.target)) {
        return
      }
      setExpanded(false)
    }
    document.addEventListener('pointerdown', handlePointerDown)
    return () => document.removeEventListener('pointerdown', handlePointerDown)
  }, [expanded])

  // 窗口尺寸变化时把悬浮球拉回视口内，保证“不遮挡、不丢失”。
  useEffect(() => {
    const handleResize = (): void => setPosition(clampPosition(positionRef.current))
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  /** 按下悬浮球：记录拖拽起点并捕获指针，后续移动即使移出球体也能持续跟踪。 */
  const handleDotPointerDown = (event: React.PointerEvent<HTMLButtonElement>): void => {
    if (event.button !== 0) return
    event.preventDefault()
    event.currentTarget.setPointerCapture(event.pointerId)
    dragRef.current = {
      pointerX: event.clientX,
      pointerY: event.clientY,
      baseX: position.x,
      baseY: position.y,
      moved: false
    }
  }

  /** 拖动悬浮球：超过点击阈值判定为拖拽，实时夹紧到视口内并收起菜单。 */
  const handleDotPointerMove = (event: React.PointerEvent<HTMLButtonElement>): void => {
    const drag = dragRef.current
    if (!drag) return
    if (
      Math.abs(event.clientX - drag.pointerX) > CLICK_SLOP ||
      Math.abs(event.clientY - drag.pointerY) > CLICK_SLOP
    ) {
      drag.moved = true
    }
    if (!drag.moved) return
    setExpanded(false)
    setPosition(
      clampPosition({
        x: drag.baseX + (event.clientX - drag.pointerX),
        y: drag.baseY + (event.clientY - drag.pointerY)
      })
    )
  }

  /** 松开悬浮球：拖拽则持久化新位置，纯点击则展开/收起功能菜单。 */
  const handleDotPointerUp = (event: React.PointerEvent<HTMLButtonElement>): void => {
    const drag = dragRef.current
    dragRef.current = null
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
    if (!drag) return
    if (drag.moved) {
      window.localStorage.setItem(POSITION_KEY, JSON.stringify(positionRef.current))
      return
    }
    setExpanded((visible) => !visible)
  }

  /** 清理演示数据：确认后清除本浏览器缓存的任务/会话/初始化状态并刷新页面。 */
  const handleCleanup = (): void => {
    Modal.confirm({
      cancelText: '取消',
      content: '将清理本浏览器中的原型会话、任务和初始化状态，静态存量应用不会被删除。',
      okText: '清理',
      onOk: () => {
        const count = clearPrototypeCache()
        message.success(`已清理 ${count} 条原型缓存`)
        window.location.reload()
      },
      title: '清理演示数据？'
    })
  }

  // 面板朝向随悬浮球位置自适应：靠近顶部向下展开，靠近右缘向左展开，始终留在视口内。
  const openDownward = position.y < 260
  const openLeftward = position.x > window.innerWidth - 288

  return (
    <div
      ref={rootRef}
      aria-label="演示辅助悬浮球"
      className={cx('show-helper')}
      style={{ left: position.x, top: position.y }}
    >
      {expanded && (
        <div
          aria-label="演示辅助功能"
          className={cx(
            'show-helper-panel',
            openDownward && 'show-helper-panel-downward',
            openLeftward && 'show-helper-panel-leftward'
          )}
          role="menu"
        >
          <div className={cx('show-helper-panel-title')}>演示辅助</div>
          <button className={cx('show-helper-action')} onClick={handleCleanup} role="menuitem" type="button">
            <ClearOutlined aria-hidden="true" />
            <span className={cx('show-helper-action-body')}>
              <strong>清理演示数据</strong>
              <small>清除浏览器缓存的任务与会话，静态存量应用保留</small>
            </span>
          </button>
        </div>
      )}
      <button
        aria-expanded={expanded}
        aria-label="演示辅助功能"
        className={cx('show-helper-dot')}
        onPointerDown={handleDotPointerDown}
        onPointerMove={handleDotPointerMove}
        onPointerUp={handleDotPointerUp}
        title="演示辅助（可拖动）"
        type="button"
      />
    </div>
  )
}
