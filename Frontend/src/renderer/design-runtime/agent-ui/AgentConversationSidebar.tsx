import React, { useMemo, useState } from 'react'
import { Button, Empty, Input, List } from 'antd5'
import { DeleteOutlined, EditOutlined, PlusOutlined, SearchOutlined } from '@ant-design/icons'

interface ConversationThread {
  id: string
  title: string
  time: string
}

interface AgentConversationSidebarProps {
  onSelect?: () => void
}

const MOCK_THREADS: ConversationThread[] = [
  { id: 'thread-1', title: '分析本周待处理事项', time: '刚刚' },
  { id: 'thread-2', title: '汇总异常记录', time: '昨天' },
  { id: 'thread-3', title: '解释审批规则', time: '周一' }
]

/** 渲染固定、可搜索的模拟会话历史。 */
export function AgentConversationSidebar({
  onSelect
}: AgentConversationSidebarProps): React.ReactElement {
  const [query, setQuery] = useState('')
  const visibleThreads = useMemo(
    () => MOCK_THREADS.filter((thread) => thread.title.includes(query.trim())),
    [query]
  )

  return (
    <aside className="x-agent-conversation__sidebar" aria-label="会话历史">
      <Button block icon={<PlusOutlined />} type="primary" data-preview-only="true">
        新建会话
      </Button>
      <Input
        allowClear
        aria-label="搜索会话"
        onChange={(event) => setQuery(event.target.value)}
        placeholder="搜索会话"
        prefix={<SearchOutlined />}
      />
      <List
        dataSource={visibleThreads}
        locale={{
          emptyText: <Empty description="没有匹配的会话" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        }}
        renderItem={(thread, index) => (
          <List.Item
            actions={[
              <Button
                aria-label={`重命名${thread.title}`}
                icon={<EditOutlined />}
                key="edit"
                size="small"
                type="text"
                data-preview-only="true"
              />,
              <Button
                aria-label={`归档${thread.title}`}
                icon={<DeleteOutlined />}
                key="archive"
                size="small"
                type="text"
                data-preview-only="true"
              />
            ]}
            className={index === 0 ? 'x-agent-conversation__thread--active' : undefined}
            onClick={onSelect}
          >
            <List.Item.Meta description={thread.time} title={thread.title} />
          </List.Item>
        )}
      />
    </aside>
  )
}
