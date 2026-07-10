import { RobotOutlined, UserOutlined } from '@ant-design/icons'
import { Spin, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { EditorMode, WorkflowRunPayload, WorkspaceCodeChangeSet } from '../../../../typings'
import { cx } from '../../../../utils'
import MarkdownContent from '../../../MarkdownContent/MarkdownContent'
import CodeChangeCard from '../CodeChangeCard'
import ToolCallCard from '../ToolCallCard'
import WorkflowRunCard, { type ClarificationAnswers } from '../WorkflowRunCard'
import type { AgentChatMessage, ChatCopy } from '../../types'
import { workflowCodeChanges } from '../../utils'
import './MessageList.less'

const { Text } = Typography

type MessageListProps = {
  copy: ChatCopy[EditorMode]
  loading: boolean
  messages: AgentChatMessage[]
  onSubmitClarification: (
    workflow: WorkflowRunPayload,
    answers: ClarificationAnswers
  ) => Promise<void>
  onOpenCodeChangeFile: (codeChanges: WorkspaceCodeChangeSet, selectedPath: string) => void
}

export default function MessageList({
  copy,
  loading,
  messages,
  onOpenCodeChangeFile,
  onSubmitClarification
}: MessageListProps): ReactElement {
  const visibleMessages = latestConversationMessages(messages)

  return (
    <div className={cx('ai-message-list')} aria-live="polite">
      <div className={cx('ai-message-column')}>
        {visibleMessages.length === 0 ? (
          <div className={cx('ai-message-empty')}>
            <span className={cx('ai-message-empty-mark')}><RobotOutlined /></span>
            <Text strong>从一个想法开始</Text>
            <Text type="secondary">{copy.empty}</Text>
          </div>
        ) : (
          visibleMessages.map((message) => {
            const codeChanges = message.codeChanges ?? workflowCodeChanges(message.workflow)
            return (
              <article className={cx('ai-message', message.role)} key={message.id}>
                <div className={cx('ai-message-author')}>
                  <span className={cx('ai-message-avatar')}>
                    {message.role === 'user' ? <UserOutlined /> : <RobotOutlined />}
                  </span>
                  <Text className={cx('ai-message-label')}>
                    {message.role === 'user' ? '你' : copy.label}
                  </Text>
                </div>
                <div className={cx('ai-message-content')}>
                  {message.role === 'assistant' ? (
                    <>
                      <MarkdownContent content={message.content} />
                      {message.toolCalls?.map((toolCall) => (
                        <ToolCallCard key={toolCall.id} toolCall={toolCall} />
                      ))}
                      {message.workflow && (
                        <WorkflowRunCard
                          disabled={loading}
                          onSubmitClarification={onSubmitClarification}
                          workflow={message.workflow}
                        />
                      )}
                      {codeChanges && (
                        <CodeChangeCard
                          codeChanges={codeChanges}
                          loading={loading}
                          onApproveAll={() => undefined}
                          onFeedback={() => undefined}
                          onOpenFile={(path) => onOpenCodeChangeFile(codeChanges, path)}
                        />
                      )}
                    </>
                  ) : (
                    <Text className={cx('ai-message-text')}>{message.content}</Text>
                  )}
                </div>
              </article>
            )
          })
        )}
        {loading && (
          <div className={cx('ai-message', 'assistant', 'loading')}>
            <span className={cx('ai-message-avatar')}><RobotOutlined /></span>
            <Spin size="small" />
            <Text type="secondary">正在运行 Workflow...</Text>
          </div>
        )}
      </div>
    </div>
  )
}

function latestConversationMessages(messages: AgentChatMessage[]): AgentChatMessage[] {
  const latestUserMessageIndex = messages.findLastIndex((message) => message.role === 'user')
  return latestUserMessageIndex >= 0 ? messages.slice(latestUserMessageIndex) : messages.slice(-1)
}
