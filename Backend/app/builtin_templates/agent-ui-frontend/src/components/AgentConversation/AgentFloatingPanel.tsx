import React, { useEffect, useRef, useState } from 'react'
import { Button, Card, Grid, Space, Tag, theme } from 'antd'
import { CloseOutlined, FullscreenOutlined, RobotOutlined } from '@ant-design/icons'
import type { AgentConversationAdapter, AgentUiTemplateConfig } from '@/typings/agentConversation'
import { AgentChatCore, createMockAgentConversationAdapter } from './AgentChatCore'
import { agentIconCompatibilityProps } from './iconCompatibility'
import { AGENT_CONVERSATION_STYLES, createAgentUiCssVariables } from './styles'

interface FloatingPoint {
  x: number
  y: number
}

export interface AgentFloatingPanelProps {
  config: AgentUiTemplateConfig
  adapter?: AgentConversationAdapter
  onOpenStandalone?: () => void
}

const LAUNCHER_SIZE = 56
const VIEWPORT_MARGIN = 16

/** 把浮动入口限制在视口内并吸附到最近的水平边缘。 */
function clampAndSnap(point: FloatingPoint): FloatingPoint {
  const maxX = Math.max(VIEWPORT_MARGIN, window.innerWidth - LAUNCHER_SIZE - VIEWPORT_MARGIN)
  const maxY = Math.max(VIEWPORT_MARGIN, window.innerHeight - LAUNCHER_SIZE - VIEWPORT_MARGIN)
  const x = Math.min(Math.max(point.x, VIEWPORT_MARGIN), maxX)
  const y = Math.min(Math.max(point.y, VIEWPORT_MARGIN), maxY)
  return { x: Math.abs(x - VIEWPORT_MARGIN) <= Math.abs(maxX - x) ? VIEWPORT_MARGIN : maxX, y }
}

/** 计算桌面浮动入口的默认右下位置。 */
function defaultFloatingPosition(): FloatingPoint {
  if (typeof window === 'undefined') return { x: VIEWPORT_MARGIN, y: VIEWPORT_MARGIN }
  return clampAndSnap({
    x: window.innerWidth - LAUNCHER_SIZE - VIEWPORT_MARGIN,
    y: window.innerHeight - LAUNCHER_SIZE - 32
  })
}

/** 管理桌面入口的拖动、视口夹紧和边缘吸附。 */
function useFloatingPosition(compact: boolean): {
  position: FloatingPoint
  dragHandlers: {
    onPointerDown: React.PointerEventHandler<HTMLButtonElement>
    onPointerMove: React.PointerEventHandler<HTMLButtonElement>
    onPointerUp: React.PointerEventHandler<HTMLButtonElement>
  }
  consumeSuppressedClick: () => boolean
} {
  const [position, setPosition] = useState(defaultFloatingPosition)
  const dragStart = useRef<{ pointer: FloatingPoint; position: FloatingPoint }>()
  const moved = useRef(false)

  useEffect(() => {
    /** 视口变化后重新夹紧入口，避免它停留在屏幕外。 */
    function handleResize(): void {
      setPosition((current) => clampAndSnap(current))
    }
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  /** 记录桌面拖动起点。 */
  function handlePointerDown(event: React.PointerEvent<HTMLButtonElement>): void {
    if (compact) return
    event.currentTarget.setPointerCapture(event.pointerId)
    dragStart.current = {
      pointer: { x: event.clientX, y: event.clientY },
      position
    }
    moved.current = false
  }

  /** 在拖动过程中实时更新受限位置。 */
  function handlePointerMove(event: React.PointerEvent<HTMLButtonElement>): void {
    if (compact || !dragStart.current) return
    const deltaX = event.clientX - dragStart.current.pointer.x
    const deltaY = event.clientY - dragStart.current.pointer.y
    moved.current = moved.current || Math.abs(deltaX) + Math.abs(deltaY) > 4
    setPosition(clampAndSnap({
      x: dragStart.current.position.x + deltaX,
      y: dragStart.current.position.y + deltaY
    }))
  }

  /** 结束拖动并执行最终边缘吸附。 */
  function handlePointerUp(event: React.PointerEvent<HTMLButtonElement>): void {
    if (compact || !dragStart.current) return
    event.currentTarget.releasePointerCapture(event.pointerId)
    dragStart.current = undefined
    setPosition((current) => clampAndSnap(current))
  }

  /** 消费拖动结束后的点击抑制标记。 */
  function consumeSuppressedClick(): boolean {
    const suppressed = moved.current
    moved.current = false
    return suppressed
  }

  return {
    position,
    dragHandlers: {
      onPointerDown: handlePointerDown,
      onPointerMove: handlePointerMove,
      onPointerUp: handlePointerUp
    },
    consumeSuppressedClick
  }
}

/** 渲染普通业务页面可组合的固定 Agent 浮动面板。 */
export function AgentFloatingPanel({
  config,
  adapter,
  onOpenStandalone
}: AgentFloatingPanelProps): React.ReactElement {
  const { token } = theme.useToken()
  const activeAdapter = React.useMemo(
    () => adapter ?? createMockAgentConversationAdapter(config),
    [adapter, config]
  )
  const screens = Grid.useBreakpoint()
  const compact = !screens.md
  const [open, setOpen] = useState(false)
  const launcher = useRef<HTMLButtonElement>(null)
  const { position, dragHandlers, consumeSuppressedClick } = useFloatingPosition(compact)

  /** 关闭面板并把焦点还给浮动入口。 */
  const closePanel = React.useCallback((): void => {
    setOpen(false)
    window.setTimeout(() => launcher.current?.focus(), 0)
  }, [])

  /** 打开面板，但忽略拖动结束产生的合成点击。 */
  function openPanel(): void {
    if (consumeSuppressedClick()) return
    setOpen(true)
  }

  useEffect(() => {
    /** 支持用 Escape 关闭当前浮动面板。 */
    function handleEscape(event: KeyboardEvent): void {
      if (event.key === 'Escape' && open) closePanel()
    }
    window.addEventListener('keydown', handleEscape)
    return () => window.removeEventListener('keydown', handleEscape)
  }, [closePanel, open])

  const chat = <AgentChatCore adapter={activeAdapter} config={config} />
  return (
    <div
      className="x-agent-ui x-agent-floating"
      data-agent-ui-template={config.templateVersion}
      data-agent-ui-surface="floating_panel"
      style={createAgentUiCssVariables(token)}
    >
      <style>{AGENT_CONVERSATION_STYLES}</style>
      <Button
        {...dragHandlers}
        aria-label={`打开${config.name}`}
        className="x-agent-floating__launcher"
        icon={<RobotOutlined {...agentIconCompatibilityProps} />}
        onClick={openPanel}
        ref={launcher}
        shape="circle"
        size="large"
        style={compact ? undefined : { left: position.x, top: position.y }}
        type="primary"
      />
      {open ? (
        <Card
          className="x-agent-floating__panel"
          extra={
            <Space>
              {config.features.maximize ? (
                <Button
                  aria-label="打开完整会话页"
                  disabled={!onOpenStandalone}
                  icon={<FullscreenOutlined {...agentIconCompatibilityProps} />}
                  onClick={onOpenStandalone}
                  type="text"
                />
              ) : null}
              <Button
                aria-label="关闭会话面板"
                icon={<CloseOutlined {...agentIconCompatibilityProps} />}
                onClick={closePanel}
                type="text"
              />
            </Space>
          }
          title={<Space><span>{config.name}</span><Tag color="processing">模拟运行</Tag></Space>}
        >
          {chat}
        </Card>
      ) : null}
    </div>
  )
}
