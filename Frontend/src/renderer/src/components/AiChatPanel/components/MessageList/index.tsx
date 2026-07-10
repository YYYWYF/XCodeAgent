import { RobotOutlined, UserOutlined } from '@ant-design/icons'
import { Empty, Spin, Typography } from 'antd'
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
      {visibleMessages.length === 0 ? (
        <Empty description={copy.empty} image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        visibleMessages.map((message) => {
          const codeChanges = message.codeChanges ?? workflowCodeChanges(message.workflow)
          return (
            <article className={cx('ai-message', message.role)} key={message.id}>
              <Text className={cx('ai-message-label')}>
                {message.role === 'user' ? (
                  <>
                    <UserOutlined /> 用户输入
                  </>
                ) : (
                  <>
                    <RobotOutlined /> {copy.label}
                  </>
                )}
              </Text>
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
            </article>
          )
        })
      )}
      {loading && (
        <div className={cx('ai-message', 'assistant', 'loading')}>
          <Spin size="small" />
          <Text type="secondary">正在运行 Workflow...</Text>
        </div>
      )}
    </div>
  )
}

function latestConversationMessages(messages: AgentChatMessage[]): AgentChatMessage[] {
  const latestUserMessageIndex = messages.findLastIndex((message) => message.role === 'user')
  return latestUserMessageIndex >= 0 ? messages.slice(latestUserMessageIndex) : messages.slice(-1)
}
