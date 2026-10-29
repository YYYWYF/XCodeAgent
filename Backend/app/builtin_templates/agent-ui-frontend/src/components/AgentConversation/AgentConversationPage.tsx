import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Avatar, Button, Empty, Space, Tag, Typography, theme } from 'antd'
import { MenuOutlined, PlusOutlined, RobotOutlined } from '@ant-design/icons'
import type {
  AgentConversationAdapter,
  AgentUiTemplateConfig,
  ConversationThread
} from '@/typings/agentConversation'
import { AgentChatCore, createMockAgentConversationAdapter } from './AgentChatCore'
import { agentIconCompatibilityProps } from './iconCompatibility'
import { AgentMobileChatDrawer } from './AgentMobileChatDrawer'
import { AGENT_CONVERSATION_STYLES, createAgentUiCssVariables } from './styles'

const { Text, Title } = Typography

export interface AgentConversationPageProps {
  config: AgentUiTemplateConfig
  adapter?: AgentConversationAdapter
}

/** 渲染桌面会话列表并保持假会话数据来自 Adapter。 */
function AgentConversationSidebar({
  threads,
  selectedThreadId,
  onCreate,
  onSelect
}: {
  threads: ConversationThread[]
  selectedThreadId?: string
  onCreate: () => void
  onSelect: (threadId: string) => void
}): React.ReactElement {
  return (
    <aside className="x-agent-conversation__sidebar" aria-label="会话历史">
      <Button
        icon={<PlusOutlined {...agentIconCompatibilityProps} />}
        onClick={onCreate}
        type="primary"
      >
        新建会话
      </Button>
      {threads.length === 0 ? <Empty description="暂无会话" image={Empty.PRESENTED_IMAGE_SIMPLE} /> : null}
      {threads.map((thread) => (
        <Button
          className={`x-agent-conversation__thread${thread.id === selectedThreadId ? ' x-agent-conversation__thread--active' : ''}`}
          key={thread.id}
          onClick={() => onSelect(thread.id)}
          type="text"
        >
          {thread.title}
        </Button>
      ))}
    </aside>
  )
}

/** 渲染生成应用的固定独立 Agent 会话页面。 */
export function AgentConversationPage({
  config,
  adapter: providedAdapter
}: AgentConversationPageProps): React.ReactElement {
  const adapter = useMemo(
    () => providedAdapter ?? createMockAgentConversationAdapter(config),
    [config, providedAdapter]
  )
  const { token } = theme.useToken()
  const [threads, setThreads] = useState<ConversationThread[]>([])
  const [selectedThreadId, setSelectedThreadId] = useState<string>()
  const [drawerOpen, setDrawerOpen] = useState(false)
  const menuButton = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    /** 首次加载时从 Adapter 获取会话列表。 */
    async function loadThreads(): Promise<void> {
      const nextThreads = await adapter.listThreads()
      setThreads(nextThreads)
      setSelectedThreadId((current) => current ?? nextThreads[0]?.id)
    }
    void loadThreads()
  }, [adapter])

  /** 创建空白会话并把它设为当前会话。 */
  async function createThread(): Promise<void> {
    const thread = await adapter.createThread()
    setThreads((current) => [thread, ...current])
    setSelectedThreadId(thread.id)
    setDrawerOpen(false)
  }

  /** 选择已有会话并关闭移动端抽屉。 */
  function selectThread(threadId: string): void {
    setSelectedThreadId(threadId)
    setDrawerOpen(false)
  }

  /** 接收聊天核心按需创建的会话并同步历史列表。 */
  function registerCreatedThread(thread: ConversationThread): void {
    setThreads((current) => [thread, ...current.filter((item) => item.id !== thread.id)])
    setSelectedThreadId(thread.id)
  }

  return (
    <main
      className="x-agent-ui x-agent-conversation"
      data-agent-ui-template={config.templateVersion}
      data-agent-ui-surface="standalone_page"
      style={createAgentUiCssVariables(token)}
    >
      <style>{AGENT_CONVERSATION_STYLES}</style>
      <AgentConversationSidebar
        onCreate={() => void createThread()}
        onSelect={selectThread}
        selectedThreadId={selectedThreadId}
        threads={threads}
      />
      <section className="x-agent-conversation__main">
        <header className="x-agent-conversation__header">
          <Space className="x-agent-conversation__identity">
            <Button
              aria-label="打开会话历史"
              className="x-agent-conversation__mobile-menu"
              icon={<MenuOutlined {...agentIconCompatibilityProps} />}
              onClick={() => setDrawerOpen(true)}
              ref={menuButton}
            />
            <Avatar icon={<RobotOutlined {...agentIconCompatibilityProps} />} />
            <div className="x-agent-conversation__identity-copy">
              <Title className="x-agent-conversation__title" level={5} style={{ margin: 0 }}>
                {config.name}
              </Title>
              <Text className="x-agent-conversation__description" type="secondary">
                {config.responsibility}
              </Text>
            </div>
          </Space>
          <Tag color="processing">模拟运行</Tag>
        </header>
        <AgentChatCore
          adapter={adapter}
          config={config}
          onThreadCreated={registerCreatedThread}
          threadId={selectedThreadId}
        />
      </section>
      <AgentMobileChatDrawer
        onClose={() => {
          setDrawerOpen(false)
          window.setTimeout(() => menuButton.current?.focus(), 0)
        }}
        onCreate={() => void createThread()}
        onSelect={selectThread}
        open={drawerOpen}
        selectedThreadId={selectedThreadId}
        threads={threads}
      />
    </main>
  )
}
