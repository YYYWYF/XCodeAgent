import type {
  CSSProperties,
  Dispatch,
  KeyboardEvent as ReactKeyboardEvent,
  MouseEvent as ReactMouseEvent,
  RefObject,
  SetStateAction
} from 'react'
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import {
  DEFAULT_ASSISTANT_PANEL_RATIO,
  DEFAULT_DIFF_PANEL_WIDTH,
  DEFAULT_PREVIEW_ASSISTANT_PANEL_RATIO
} from '../constants'
import type { RightPanelLayout, RightPanelState } from '../types'
import { clampAssistantPanelRatio } from '../utils'

type AssistantPreviewLayout = {
  assistantPanelRatio: number
  embeddedPreviewOpen: boolean
  handlePanelSplitKeyDown: (event: ReactKeyboardEvent<HTMLDivElement>) => void
  handlePanelSplitDragStart: (event: ReactMouseEvent<HTMLDivElement>) => void
  panelRef: RefObject<HTMLElement>
  panelStyle?: CSSProperties
  rightPanelLayout: RightPanelLayout
  rightPanel?: RightPanelState
  setRightPanelLayout: Dispatch<SetStateAction<RightPanelLayout>>
  setRightPanel: (panel?: RightPanelState) => void
  splitDragging: boolean
}

/** 管理右侧工作区的三档布局（隐藏/分栏/全宽）、分栏宽度、拖拽及预览初始比例。 */
export function useAssistantPreviewLayout(): AssistantPreviewLayout {
  const panelRef = useRef<HTMLElement | null>(null)
  const [rightPanel, setRightPanel] = useState<RightPanelState>()
  const previousRightPanelTypeRef = useRef<RightPanelState['type']>()
  const [assistantPanelRatio, setAssistantPanelRatio] = useState(DEFAULT_ASSISTANT_PANEL_RATIO)
  // 三档布局取代了原先布尔式的 rightPanelOpen：隐藏与全宽在尺寸上互斥，
  // 用单一状态源避免"开着但铺满"这类组合态。
  const [rightPanelLayout, setRightPanelLayout] = useState<RightPanelLayout>('split')
  const [splitDragging, setSplitDragging] = useState(false)
  const embeddedPreviewOpen = rightPanel?.type === 'preview'
  // 只有分栏态需要注入宽度变量；隐藏与全宽由 CSS 决定尺寸。
  const panelStyle =
    rightPanelLayout === 'split'
      ? ({
          '--assistant-panel-width': `${(assistantPanelRatio * 100).toFixed(4)}%`
        } as CSSProperties)
      : undefined

  /** 切入预览时扩宽右侧面板；切入 Diff 时按目标宽度初始化，之后允许拖拽。 */
  useLayoutEffect(() => {
    const previousType = previousRightPanelTypeRef.current
    previousRightPanelTypeRef.current = rightPanel?.type
    if (rightPanel?.type === 'preview' && previousType !== 'preview') {
      setAssistantPanelRatio(DEFAULT_PREVIEW_ASSISTANT_PANEL_RATIO)
      return
    }
    if (rightPanel?.type !== 'diff' || previousType === 'diff') return

    const panelWidth = panelRef.current?.getBoundingClientRect().width ?? 0
    if (panelWidth <= 0) return
    setAssistantPanelRatio(
      clampAssistantPanelRatio((panelWidth - DEFAULT_DIFF_PANEL_WIDTH) / panelWidth)
    )
  }, [rightPanel?.type])

  useEffect(() => {
    // 只有分栏态才有宽度可言：隐藏与全宽都不参与比例约束，
    // 且切离分栏时要终止拖拽，否则会留下全局 col-resize 光标。
    if (rightPanelLayout !== 'split') {
      setSplitDragging(false)
      return
    }

    const nextRatio = clampAssistantPanelRatio(assistantPanelRatio)
    if (nextRatio !== assistantPanelRatio) {
      setAssistantPanelRatio(nextRatio)
    }
  }, [assistantPanelRatio, rightPanelLayout])

  useEffect(() => {
    if (rightPanelLayout !== 'split') return undefined

    /** 在窗口尺寸改变后重新约束左右面板比例。 */
    const handleWindowResize = (): void => {
      setAssistantPanelRatio((currentRatio) => clampAssistantPanelRatio(currentRatio))
    }

    window.addEventListener('resize', handleWindowResize)
    return () => window.removeEventListener('resize', handleWindowResize)
  }, [rightPanelLayout])

  useEffect(() => {
    if (!splitDragging) return undefined

    const previousCursor = document.body.style.cursor
    const previousUserSelect = document.body.style.userSelect
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    const handleMouseMove = (event: MouseEvent): void => {
      const panelRect = panelRef.current?.getBoundingClientRect()
      if (!panelRect || panelRect.width <= 0) return

      setAssistantPanelRatio(
        clampAssistantPanelRatio((event.clientX - panelRect.left) / panelRect.width)
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

  /** 支持使用方向键无障碍调整左右面板宽度，每次步进约 2% 比例。 */
  const handlePanelSplitKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>): void => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
    event.preventDefault()
    const direction = event.key === 'ArrowLeft' ? -1 : 1
    setAssistantPanelRatio((currentRatio) =>
      clampAssistantPanelRatio(currentRatio + direction * 0.02)
    )
  }

  return {
    assistantPanelRatio,
    embeddedPreviewOpen,
    handlePanelSplitKeyDown,
    handlePanelSplitDragStart,
    panelRef,
    panelStyle,
    rightPanelLayout,
    rightPanel,
    setRightPanelLayout,
    setRightPanel,
    splitDragging
  }
}
