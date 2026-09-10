import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Button, Card, ConfigProvider, Space, Typography, theme } from 'antd5'
import { CloseOutlined, FullscreenOutlined, RobotOutlined } from '@ant-design/icons'
import { AgentChatCore } from './AgentChatCore'
import { parseAgentUiTemplateConfig } from './contracts'
import { AGENT_UI_STYLES, createAgentUiCssVariables } from './styles'
import type { AgentPreviewState } from './types'
import { useFloatingPosition } from './useFloatingPosition'

const { Text } = Typography

interface AgentFloatingPanelTemplateProps {
  configJson: string
}

interface AgentFloatingContentProps extends AgentFloatingPanelTemplateProps {
  onToggleTheme: () => void
  dark: boolean
}

/** 在主题 Provider 内渲染业务页面上的固定浮动 Agent 外壳。 */
function AgentFloatingContent({
  configJson,
  onToggleTheme,
  dark
}: AgentFloatingContentProps): React.ReactElement {
  const config = useMemo(() => parseAgentUiTemplateConfig(configJson), [configJson])
  const { token } = theme.useToken()
  const [open, setOpen] = useState(false)
  const [state, setState] = useState<AgentPreviewState>('normal')
  const launcherRef = useRef<HTMLButtonElement>(null)
  const { position, compact, dragHandlers, consumeClickSuppressed } = useFloatingPosition()

  /** 关闭面板并把键盘焦点还给浮动入口。 */
  function closePanel(): void {
    setOpen(false)
    window.setTimeout(() => launcherRef.current?.focus(), 0)
  }

  /** 打开面板，但忽略桌面拖动结束产生的合成点击。 */
  function openPanel(): void {
    if (consumeClickSuppressed()) return
    setOpen(true)
  }

  useEffect(() => {
    /** 支持使用 Escape 关闭当前 Agent 面板。 */
    function handleEscape(event: KeyboardEvent): void {
      if (event.key === 'Escape' && open) closePanel()
    }
    window.addEventListener('keydown', handleEscape)
    return () => window.removeEventListener('keydown', handleEscape)
  }, [open])

  const chat = <AgentChatCore config={config} state={state} onStateChange={setState} />
  return (
    <div
      className="x-agent-ui x-agent-floating"
      style={createAgentUiCssVariables(token)}
      data-agent-template-module="@xcodeagent/agent-ui-design"
      data-agent-template-version={config.templateVersion}
    >
      <style>{AGENT_UI_STYLES}</style>
      <Button
        {...dragHandlers}
        aria-label={`打开${config.name}`}
        className="x-agent-floating__launcher"
        icon={<RobotOutlined />}
        onClick={openPanel}
        ref={launcherRef}
        shape="circle"
        size="large"
        style={compact ? undefined : { left: position.x, top: position.y }}
        type="primary"
        data-action-id={config.actionId}
        data-agent-id={config.agentId}
        data-agent-part="launcher"
        data-agent-surface="floating_panel"
        data-control-id={`${config.agentId}-launcher`}
      />
      {open ? (
        <Card
          className="x-agent-floating__panel"
          extra={
            <Space>
              {config.features.maximize ? (
                <Button
                  aria-label="打开完整会话页"
                  icon={<FullscreenOutlined />}
                  type="text"
                  data-preview-only="true"
                />
              ) : null}
              <Button
                aria-label="关闭会话面板"
                data-preview-only="true"
                icon={<CloseOutlined />}
                onClick={closePanel}
                type="text"
              />
              <Button onClick={onToggleTheme} type="text" data-preview-only="true">
                {dark ? '浅色' : '深色'}
              </Button>
            </Space>
          }
          title={
            <Space>
              <span>{config.name}</span>
              <Text className="x-agent-floating__simulated">模拟运行</Text>
            </Space>
          }
          data-agent-id={config.agentId}
          data-agent-part="panel"
          data-agent-surface="floating_panel"
          data-control-id={`${config.agentId}-panel`}
        >
          {chat}
        </Card>
      ) : null}
    </div>
  )
}

/** 渲染使用固定入口和自适应面板的 Agent 浮层。 */
export function AgentFloatingPanelTemplate({
  configJson
}: AgentFloatingPanelTemplateProps): React.ReactElement {
  const [dark, setDark] = useState(false)
  return (
    <ConfigProvider theme={{ algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm }}>
      <AgentFloatingContent
        configJson={configJson}
        dark={dark}
        onToggleTheme={() => setDark((current) => !current)}
      />
    </ConfigProvider>
  )
}
