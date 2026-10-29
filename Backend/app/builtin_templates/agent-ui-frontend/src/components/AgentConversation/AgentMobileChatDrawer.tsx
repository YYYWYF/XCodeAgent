import React from 'react'
import { Button, Drawer, Empty, Space } from 'antd'
import type { ConversationThread } from '@/typings/agentConversation'

interface AgentMobileChatDrawerProps {
  open: boolean
  threads: ConversationThread[]
  selectedThreadId?: string
  onClose: () => void
  onCreate: () => void
  onSelect: (threadId: string) => void
}

/** 渲染窄屏下的独立会话页历史抽屉。 */
export function AgentMobileChatDrawer(
  props: AgentMobileChatDrawerProps
): React.ReactElement {
  return (
    <Drawer
      onClose={props.onClose}
      open={props.open}
      placement="left"
      title="会话历史"
      width="88%"
    >
      <Space direction="vertical" size="middle" style={{ display: 'flex' }}>
        <Button block onClick={props.onCreate} type="primary">新建会话</Button>
        {props.threads.length === 0 ? (
          <Empty description="暂无会话" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : null}
        {props.threads.map((thread) => (
          <Button
            block
            key={thread.id}
            onClick={() => props.onSelect(thread.id)}
            type={thread.id === props.selectedThreadId ? 'primary' : 'default'}
          >
            {thread.title}
          </Button>
        ))}
      </Space>
    </Drawer>
  )
}
