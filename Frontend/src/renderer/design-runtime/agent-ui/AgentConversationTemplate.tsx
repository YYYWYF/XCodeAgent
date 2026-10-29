import React, { useMemo, useState } from 'react'
import { Avatar, Button, ConfigProvider, Drawer, Space, Typography, theme } from 'antd5'
import { MenuOutlined, RobotOutlined } from '@ant-design/icons'
import { AgentChatCore } from './AgentChatCore'
import { AgentConversationSidebar } from './AgentConversationSidebar'
import { AGENT_UI_STYLES, createAgentUiCssVariables } from './styles'
import { parseAgentUiTemplateConfig } from './contracts'
import type { AgentPreviewState } from './types'

const { Text, Title } = Typography

interface AgentConversationTemplateProps {
  configJson: string
}

/** 在主题 Provider 内渲染独立会话页的固定结构。 */
function AgentConversationContent({
  configJson
}: AgentConversationTemplateProps): React.ReactElement {
  const config = useMemo(() => parseAgentUiTemplateConfig(configJson), [configJson])
  const { token } = theme.useToken()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [previewState, setPreviewState] = useState<AgentPreviewState>('normal')

  return (
    <main
      className="x-agent-ui x-agent-conversation"
      style={createAgentUiCssVariables(token)}
      data-agent-template-module="@xcodeagent/agent-ui-design"
      data-agent-template-version={config.templateVersion}
    >
      <style>{AGENT_UI_STYLES}</style>
      <AgentConversationSidebar />
      <section className="x-agent-conversation__main">
        <header className="x-agent-conversation__header">
          <Space className="x-agent-conversation__identity">
            <Button
              aria-label="打开会话历史"
              className="x-agent-conversation__mobile-menu"
              icon={<MenuOutlined />}
              onClick={() => setDrawerOpen(true)}
            />
            <Avatar icon={<RobotOutlined />} />
            <div className="x-agent-conversation__identity-copy">
              <Title className="x-agent-conversation__title" level={5} style={{ margin: 0 }}>
                {config.name}
              </Title>
              <Text className="x-agent-conversation__description" type="secondary">
                {config.responsibility}
              </Text>
            </div>
          </Space>
        </header>
        <AgentChatCore config={config} state={previewState} onStateChange={setPreviewState} />
      </section>
      <Drawer
        onClose={() => setDrawerOpen(false)}
        open={drawerOpen}
        placement="left"
        title="会话历史"
        width="88%"
      >
        <AgentConversationSidebar onSelect={() => setDrawerOpen(false)} />
      </Drawer>
    </main>
  )
}

/** 渲染支持明暗主题切换的固定独立 Agent 会话页。 */
export function AgentConversationTemplate({
  configJson
}: AgentConversationTemplateProps): React.ReactElement {
  const [dark, setDark] = useState(false)
  return (
    <ConfigProvider theme={{ algorithm: dark ? theme.darkAlgorithm : theme.defaultAlgorithm }}>
      <div style={{ height: '100%', position: 'relative' }}>
        <Button
          onClick={() => setDark((current) => !current)}
          style={{ position: 'fixed', right: 16, top: 16, zIndex: 1100 }}
          data-preview-only="true"
        >
          {dark ? '浅色' : '深色'}
        </Button>
        <AgentConversationContent configJson={configJson} />
      </div>
    </ConfigProvider>
  )
}
