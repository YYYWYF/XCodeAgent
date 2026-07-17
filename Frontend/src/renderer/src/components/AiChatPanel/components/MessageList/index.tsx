import { CheckCircleOutlined, RobotOutlined, ToolOutlined } from '@ant-design/icons'
import { Spin, Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { EditorMode, WorkflowRunPayload, WorkspaceCodeChangeSet } from '../../../../typings'
import { cx } from '../../../../utils'
import MarkdownContent from '../../../MarkdownContent/MarkdownContent'
import CodeChangeCard from '../CodeChangeCard'
import ToolCallCard from '../ToolCallCard'
import ProcessSteps from '../ProcessSteps'
import WorkflowRunCard, { type ClarificationAnswers } from '../WorkflowRunCard'
import type { AgentChatMessage, ChatCopy } from '../../types'
import { workflowCodeChanges } from '../../utils'
import './MessageList.less'

const { Text } = Typography

type MessageListProps = {
  codeChangeActionsDisabled: boolean
  copy: ChatCopy[EditorMode]
  loading: boolean
  messages: AgentChatMessage[]
  onRevertCodeChanges: (messageId: number, codeChanges: WorkspaceCodeChangeSet) => void
  onSubmitClarification: (
    workflow: WorkflowRunPayload,
    answers: ClarificationAnswers
  ) => Promise<void>
  onOpenCodeChangeFile: (codeChanges: WorkspaceCodeChangeSet, selectedPath: string) => void
  revertingCodeChangeIds: ReadonlySet<string>
}

export default function MessageList({
  codeChangeActionsDisabled,
  copy,
  loading,
  messages,
  onOpenCodeChangeFile,
  onRevertCodeChanges,
  revertingCodeChangeIds,
  onSubmitClarification
}: MessageListProps): ReactElement {
  const activeAssistantMessageId = loading ? findLastAssistantMessageId(messages) : undefined
  const hasStreamingProcess = messages.some(
    (message) => message.id === activeAssistantMessageId && Boolean(message.processSteps?.length)
  )

  return (
    <div className={cx('ai-message-list')} aria-live="polite">
      <div className={cx('ai-message-column')}>
        {messages.length === 0 ? (
          <div className={cx('ai-message-empty')}>
            <span className={cx('ai-message-empty-mark')}>
              <RobotOutlined />
            </span>
            <Text strong>从一个想法开始</Text>
            <Text type="secondary">{copy.empty}</Text>
          </div>
        ) : (
          messages.map((message) => {
            const messageLoading = message.id === activeAssistantMessageId
            const codeChanges = message.codeChanges ?? workflowCodeChanges(message.workflow)
            const nonToolSteps = message.processSteps?.filter(
              (step) => step.kind !== 'tool' && step.kind !== 'command'
            )
            const requiresClarification =
              message.workflow?.summary.clarification?.status === 'requires_user_input'
            return (
              <article
                className={cx(
                  'ai-message',
                  message.role,
                  message.role === 'assistant' && !messageLoading && 'completed'
                )}
                key={message.id}
              >
                <div className={cx('ai-message-content')}>
                  {message.role === 'assistant' ? (
                    <>
                      {messageLoading && nonToolSteps && nonToolSteps.length > 0 && (
                        <ProcessSteps loading={messageLoading} steps={nonToolSteps} />
                      )}
                      {messageLoading &&
                        message.toolCalls?.map((toolCall) => (
                          <ToolCallCard key={toolCall.id} toolCall={toolCall} />
                        ))}
                      {!messageLoading && codeChanges && (
                        <div className={cx('final-result-heading')}>
                          <span>
                            <CheckCircleOutlined />
                          </span>
                          <div>
                            <Text strong>任务已完成</Text>
                            <Text type="secondary">最终结果</Text>
                          </div>
                        </div>
                      )}
                      <div className={cx(!messageLoading && codeChanges && 'final-result-content')}>
                        <MarkdownContent content={message.content} />
                      </div>
                      {message.workflow && (messageLoading || requiresClarification) && (
                        <WorkflowRunCard
                          disabled={messageLoading}
                          onSubmitClarification={onSubmitClarification}
                          workflow={message.workflow}
                        />
                      )}
                      {!messageLoading && codeChanges && (
                        <CodeChangeCard
                          codeChanges={codeChanges}
                          loading={messageLoading}
                          onApproveAll={() => undefined}
                          onOpenFile={(path) => onOpenCodeChangeFile(codeChanges, path)}
                          onRevert={() => onRevertCodeChanges(message.id, codeChanges)}
                          revertDisabled={codeChangeActionsDisabled}
                          reverting={revertingCodeChangeIds.has(codeChanges.id)}
                        />
                      )}
                    </>
                  ) : (
                    <>
                      {message.skills && message.skills.length > 0 && (
                        <div className={cx('message-skill-labels')}>
                          {message.skills.map((skill) => (
                            <Tag key={skill.name} title={skill.description}>
                              <ToolOutlined />
                              <span>{skill.name}</span>
                            </Tag>
                          ))}
                        </div>
                      )}
                      <Text className={cx('ai-message-text')}>{message.content}</Text>
                    </>
                  )}
                </div>
              </article>
            )
          })
        )}
        {loading && !hasStreamingProcess && (
          <div className={cx('ai-message', 'assistant', 'loading')}>
            <Spin size="small" />
            <Text type="secondary">正在运行 Workflow...</Text>
          </div>
        )}
      </div>
    </div>
  )
}

function findLastAssistantMessageId(messages: AgentChatMessage[]): number | undefined {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].role === 'assistant') return messages[index].id
  }
  return undefined
}
