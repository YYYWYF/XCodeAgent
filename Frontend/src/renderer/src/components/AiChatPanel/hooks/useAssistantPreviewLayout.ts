import type {
  CSSProperties,
  KeyboardEvent as ReactKeyboardEvent,
  MouseEvent as ReactMouseEvent,
  RefObject
} from 'react'
import { useEffect, useRef, useState } from 'react'
import { DEFAULT_ASSISTANT_PANEL_WIDTH } from '../constants'
import type { RightPanelState } from '../types'
import { clampAssistantPanelWidth } from '../utils'

type AssistantPreviewLayout = {
  assistantPanelWidth: number
  embeddedPreviewOpen: boolean
  handlePanelSplitKeyDown: (event: ReactKeyboardEvent<HTMLDivElement>) => void
  handlePanelSplitDragStart: (event: ReactMouseEvent<HTMLDivElement>) => void
  panelRef: RefObject<HTMLElement>
  panelStyle?: CSSProperties
  rightPanel?: RightPanelState
  rightPanelOpen: boolean
  setRightPanel: (panel?: RightPanelState) => void
  splitDragging: boolean
}

export function useAssistantPreviewLayout(): AssistantPreviewLayout {
  const panelRef = useRef<HTMLElement | null>(null)
  const [rightPanel, setRightPanel] = useState<RightPanelState>()
  const [assistantPanelWidth, setAssistantPanelWidth] = useState(DEFAULT_ASSISTANT_PANEL_WIDTH)
  const [splitDragging, setSplitDragging] = useState(false)
  const embeddedPreviewOpen = rightPanel?.type === 'preview'
  const rightPanelOpen = Boolean(rightPanel)
  const panelStyle = rightPanelOpen
    ? ({
        '--assistant-panel-width': `${assistantPanelWidth}px`
      } as CSSProperties)
    : undefined

  useEffect(() => {
    if (!rightPanelOpen) {
      setSplitDragging(false)
      return
    }

    const nextWidth = clampAssistantPanelWidth(assistantPanelWidth, panelRef.current)
    if (nextWidth !== assistantPanelWidth) {
      setAssistantPanelWidth(nextWidth)
    }
  }, [assistantPanelWidth, rightPanelOpen])

  useEffect(() => {
    if (!rightPanelOpen) return undefined

    /** 在窗口尺寸改变后重新约束左右面板宽度。 */
    const handleWindowResize = (): void => {
      setAssistantPanelWidth((currentWidth) =>
        clampAssistantPanelWidth(currentWidth, panelRef.current)
      )
    }

    window.addEventListener('resize', handleWindowResize)
    return () => window.removeEventListener('resize', handleWindowResize)
  }, [rightPanelOpen])

  useEffect(() => {
    if (!splitDragging) return undefined

    const previousCursor = document.body.style.cursor
    const previousUserSelect = document.body.style.userSelect
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    const handleMouseMove = (event: MouseEvent): void => {
      const panelRect = panelRef.current?.getBoundingClientRect()
      if (!panelRect) return

      setAssistantPanelWidth(
        clampAssistantPanelWidth(event.clientX - panelRect.left, panelRef.current)
      )
    }
    const handleMouseUp = (): void => setSplitDragging(false)

    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)

    return () => {
      document.body.style.cursor = previousCursor
      document.body.style.userSelect = previousUserSelect
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
    }
  }, [splitDragging])

  const handlePanelSplitDragStart = (event: ReactMouseEvent<HTMLDivElement>): void => {
    event.preventDefault()
    setSplitDragging(true)
  }

  /** 支持使用方向键无障碍调整左右面板宽度。 */
  const handlePanelSplitKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>): void => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
    event.preventDefault()
    const direction = event.key === 'ArrowLeft' ? -1 : 1
    setAssistantPanelWidth((currentWidth) =>
      clampAssistantPanelWidth(currentWidth + direction * 24, panelRef.current)
    )
  }

  return {
    assistantPanelWidth,
    embeddedPreviewOpen,
    handlePanelSplitKeyDown,
    handlePanelSplitDragStart,
    panelRef,
    panelStyle,
    rightPanel,
    rightPanelOpen,
    setRightPanel,
    splitDragging
  }
}
