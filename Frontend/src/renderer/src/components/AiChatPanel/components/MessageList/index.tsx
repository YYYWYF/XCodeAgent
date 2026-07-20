import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  RobotOutlined,
  ToolOutlined
} from '@ant-design/icons'
import { Spin, Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { EditorMode, WorkflowRunPayload, WorkspaceCodeChangeSet } from '../../../../typings'
import { cx } from '../../../../utils'
import MarkdownContent from '../../../MarkdownContent/MarkdownContent'
import CodeChangeCard from '../CodeChangeCard'
import ToolCallCard from '../ToolCallCard'
import ProcessSteps from '../ProcessSteps'
import WorkflowRunCard, { type ClarificationAnswers } from '../WorkflowRunCard'
import { readIntegrationTestChecks } from '../../../../service/agUiAgent'
import type { ProcessStepRecord } from '../../../../service/agUiAgent'
import type { AgentChatMessage, ChatCopy } from '../../types'
import { workflowCodeChanges, workflowFinalResultPresentation } from '../../utils'
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

/** 渲染聊天消息、Workflow 最终状态和代码变更操作。 */
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
            const finalResult = workflowFinalResultPresentation(message.workflow)
            const nonToolSteps = stepsWithIntegrationTestChecks(
              message.processSteps,
              message.workflow
            )?.filter((step) => step.kind !== 'tool' && step.kind !== 'command')
            const requiresClarification =
              message.workflow?.summary.clarification?.status === 'requires_user_input'
            const hasScopedBuildProgress = workflowHasScopedBuildProgress(message.workflow)
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
                      {nonToolSteps && nonToolSteps.length > 0 && (
                        <ProcessSteps loading={messageLoading} steps={nonToolSteps} />
                      )}
                      {messageLoading &&
                        message.toolCalls?.map((toolCall) => (
                          <ToolCallCard key={toolCall.id} toolCall={toolCall} />
                        ))}
                      {!messageLoading && codeChanges && (
                        <div className={cx('final-result-heading', finalResult.failed && 'failed')}>
                          <span>
                            {finalResult.failed ? <CloseCircleOutlined /> : <CheckCircleOutlined />}
                          </span>
                          <div>
                            <Text strong>{finalResult.title}</Text>
                            <Text type="secondary">最终结果</Text>
                          </div>
                        </div>
                      )}
                      <div className={cx(!messageLoading && codeChanges && 'final-result-content')}>
                        <MarkdownContent content={message.content} />
                      </div>
                      {message.workflow && (messageLoading || requiresClarification || hasScopedBuildProgress) && (
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

/** 从最终工作流快照回填集成测试清单，确保会话恢复后仍可展示检查项。 */
function stepsWithIntegrationTestChecks(
  steps: ProcessStepRecord[] | undefined,
  workflow: WorkflowRunPayload | undefined
): ProcessStepRecord[] | undefined {
  if (!steps?.length) return steps
  const finalChecks = completedIntegrationTestChecks(workflow)
  if (!finalChecks?.length) return steps

  return steps.map((step) => {
    if (step.id !== 'workflow:integration_test') return step
    return {
      ...step,
      checks: mergeIntegrationTestChecks(step.checks, finalChecks)
    }
  })
}

/** 从 integration_test 完成事件或状态快照读取最终检查清单。 */
function completedIntegrationTestChecks(
  workflow: WorkflowRunPayload | undefined
): ReturnType<typeof readIntegrationTestChecks> {
  if (!workflow) return undefined
  const event = [...workflow.events]
    .reverse()
    .find((item) => item.nodeName === 'integration_test' && item.type === 'workflow.node.completed')
  const eventChecks = readIntegrationTestChecks(event?.data?.testReport)
  if (eventChecks?.length) return eventChecks
  return readIntegrationTestChecks(workflow.state?.testReport)
}

/** 按稳定检查 id 合并实时与完成态快照，完成态覆盖同名检查的最终结果。 */
function mergeIntegrationTestChecks(
  current: ProcessStepRecord['checks'],
  finalChecks: NonNullable<ReturnType<typeof readIntegrationTestChecks>>
): NonNullable<ProcessStepRecord['checks']> {
  const checksById = new Map(current?.map((check) => [check.id, check]) ?? [])
  for (const check of finalChecks) checksById.set(check.id, check)
  return [...checksById.values()]
}

function workflowHasScopedBuildProgress(workflow?: WorkflowRunPayload): boolean {
  /** 判断当前消息是否包含页面或数据源范围的构建进度。 */

  const state = workflow?.state || {}
  const result = workflow?.result || {}
  const slice = state.buildExecutionSlice || state.build_execution_slice
    || result.buildExecutionSlice || result.build_execution_slice
  if (!slice || typeof slice !== 'object') return false
  const scope = (slice as { scope?: { type?: unknown } }).scope
  return scope?.type === 'page' || scope?.type === 'data_source'
}

function findLastAssistantMessageId(messages: AgentChatMessage[]): number | undefined {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].role === 'assistant') return messages[index].id
  }
  return undefined
}
