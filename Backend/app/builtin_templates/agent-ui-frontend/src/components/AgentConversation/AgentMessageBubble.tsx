import React from 'react'
import { Avatar, Button, Card, Space, Tag, Typography } from 'antd'
import { RobotOutlined, ToolOutlined, UserOutlined } from '@ant-design/icons'
import type { AgentMessage } from '@/typings/agentConversation'
import { agentIconCompatibilityProps } from './iconCompatibility'

const { Paragraph, Text } = Typography

export interface AgentMessageBubbleProps {
  message: AgentMessage
  disabled: boolean
  onApproval: (message: AgentMessage, approved: boolean) => void
}

/** 渲染消息内的固定 Tool 摘要与审批卡片。 */
function AgentMessageDetails({
  message,
  disabled,
  onApproval
}: AgentMessageBubbleProps): React.ReactElement | null {
  return (
    <>
      {message.tool ? (
        <Card className="x-agent-chat__tool" size="small">
          <Space wrap>
            <ToolOutlined {...agentIconCompatibilityProps} />
            <Text strong>{message.tool.title}</Text>
            <Tag color={message.tool.status === 'error' ? 'error' : 'success'}>
              {message.tool.status === 'error' ? '失败' : '已完成'}
            </Tag>
          </Space>
          <Paragraph type="secondary">{message.tool.detail}</Paragraph>
        </Card>
      ) : null}
      {message.approval ? (
        <Card className="x-agent-chat__approval" size="small">
          <Text strong>{message.approval.title}</Text>
          <Paragraph type="secondary">{message.approval.detail}</Paragraph>
          {message.approval.status === 'pending' ? (
            <Space>
              <Button
                disabled={disabled}
                onClick={() => onApproval(message, true)}
                type="primary"
              >
                批准
              </Button>
              <Button disabled={disabled} onClick={() => onApproval(message, false)}>
                拒绝
              </Button>
            </Space>
          ) : (
            <Tag color={message.approval.status === 'approved' ? 'success' : 'default'}>
              {message.approval.status === 'approved' ? '已批准' : '已拒绝'}
            </Tag>
          )}
        </Card>
      ) : null}
    </>
  )
}

/** 渲染两种 Surface 共用的单条左右气泡消息。 */
export function AgentMessageBubble({
  message,
  disabled,
  onApproval
}: AgentMessageBubbleProps): React.ReactElement {
  const user = message.role === 'user'
  return (
    <article className={`x-agent-message${user ? ' x-agent-message--user' : ''}`}>
      <Avatar
        icon={
          user ? (
            <UserOutlined {...agentIconCompatibilityProps} />
          ) : (
            <RobotOutlined {...agentIconCompatibilityProps} />
          )
        }
      />
      <div className="x-agent-message__body">
        <Text className="x-agent-message__author">{message.author}</Text>
        <div className="x-agent-message__bubble">
          <Paragraph>{message.content}</Paragraph>
          <AgentMessageDetails
            disabled={disabled}
            message={message}
            onApproval={onApproval}
          />
        </div>
      </div>
    </article>
  )
}
