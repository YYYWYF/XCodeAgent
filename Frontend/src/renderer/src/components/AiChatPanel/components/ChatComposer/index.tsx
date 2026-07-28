import {
  BugOutlined,
  FileSearchOutlined,
  SendOutlined,
  StopOutlined,
  ToolOutlined
} from '@ant-design/icons'
import { Alert, Button, Input, Select, Tag, Tooltip, Typography } from 'antd'
import type { KeyboardEvent, ReactElement } from 'react'
import { useState } from 'react'
import type {
  ChatMessageSkill,
  EditorMode,
  WorkflowBuildExecutionScope,
  WorkflowDebugOptions,
  WorkflowRunPayload
} from '../../../../typings'
import { cx } from '../../../../utils'
import { skillsAfterEmptyBackspace } from '../../skillSelection'
import type { ChatCopy } from '../../types'
import ResourceSkillMenu from './ResourceSkillMenu'
import './ChatComposer.less'

const { Text } = Typography
const { TextArea } = Input
const { Option } = Select

const resumeNodeOptions = [
  { value: 'detail_confirmation', label: 'detail_confirmation' },
  { value: 'inspect_workspace', label: 'inspect_workspace' },
  { value: 'prepare_build_tasks', label: 'prepare_build_tasks' },
  { value: 'build', label: 'build' },
  { value: 'integration_test', label: 'integration_test' },
  { value: 'launch_project', label: 'launch_project' },
  { value: 'acceptance', label: 'acceptance' },
  { value: 'finalize_project', label: 'finalize_project' }
]

const buildScopeOptions: Array<{ value: WorkflowBuildExecutionScope['type']; label: string }> = [
  { value: 'application', label: '整个应用' },
  { value: 'page', label: '单个页面' },
  { value: 'data_source', label: '单个数据源' },
  { value: 'endpoint', label: '单个接口' }
]

type ChatComposerProps = {
  activeWorkflow?: WorkflowRunPayload
  copy: ChatCopy[EditorMode]
  debugOnly?: boolean
  draft: string
  error?: string
  initialResumeFrom?: string
  loading: boolean
  onDraftChange: (value: string) => void
  onSelectedSkillsChange: (skills: ChatMessageSkill[]) => void
  onSend: (workflowDebug?: WorkflowDebugOptions) => Promise<void>
  onStopGenerating: () => void
  stopping: boolean
  selectedSkills: ChatMessageSkill[]
  workspaceBusy: boolean
  workspaceRoot: string
}

export default function ChatComposer({
  activeWorkflow,
  copy,
  debugOnly = false,
  draft,
  error,
  initialResumeFrom = 'detail_confirmation',
  loading,
  onDraftChange,
  onSelectedSkillsChange,
  onSend,
  onStopGenerating,
  stopping,
  selectedSkills,
  workspaceBusy,
  workspaceRoot
}: ChatComposerProps): ReactElement {
  const [debugEnabled, setDebugEnabled] = useState(debugOnly)
  const [traceOpen, setTraceOpen] = useState(false)
  const [resumeFrom, setResumeFrom] = useState(initialResumeFrom)
  const [buildScopeType, setBuildScopeType] =
    useState<WorkflowBuildExecutionScope['type']>('application')
  const [buildScopeTargetId, setBuildScopeTargetId] = useState('')
  const hasDebugNode = !debugEnabled || Boolean(resumeFrom)
  const isBuildTaskDebug = debugEnabled && resumeFrom === 'prepare_build_tasks'
  const hasBuildScopeTarget = buildScopeType === 'application' || Boolean(buildScopeTargetId.trim())
  const canSend = debugEnabled
    ? hasDebugNode && (!isBuildTaskDebug || hasBuildScopeTarget)
    : Boolean(draft.trim())

  /** 根据调试开关生成 Workflow 调试参数，并在任务拆分节点附带分层 DAG 范围。 */
  const currentDebugOptions = (): WorkflowDebugOptions | undefined =>
    debugEnabled
      ? {
          enabled: true,
          resumeFrom,
          ...(isBuildTaskDebug
            ? {
                buildExecutionScope: {
                  type: buildScopeType,
                  ...(buildScopeType === 'application'
                    ? {}
                    : { targetId: buildScopeTargetId.trim() })
                }
              }
            : {})
        }
      : undefined

  /** 校验当前状态并提交对话内容。 */
  const handleSend = (): void => {
    if (!hasDebugNode) return
    onSend(currentDebugOptions())
  }

  /** 处理输入区键盘操作，空文本时 Backspace 依次删除最后一个技能标签。 */
  const handleInputKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>): void => {
    const nextSkills = skillsAfterEmptyBackspace(event.key, draft, selectedSkills)
    if (!nextSkills || loading) return
    event.preventDefault()
    onSelectedSkillsChange(nextSkills)
  }

  return (
    <div className={cx('ai-chat-composer', debugOnly && 'debug-only')}>
      <div className={cx('ai-chat-composer-column')}>
        {error && <Alert message={error} showIcon type="error" />}
        <div className={cx('ai-chat-composer-frame')}>
          <div className={cx('composer-inline-input')}>
            {!debugOnly && selectedSkills.length > 0 && (
              <div className={cx('composer-selected-skills')}>
                {selectedSkills.map((skill) => (
                  <Tag
                    closable={!loading}
                    key={skill.name}
                    onClose={() =>
                      onSelectedSkillsChange(
                        selectedSkills.filter((item) => item.name !== skill.name)
                      )
                    }
                    title={skill.description}
                  >
                    <ToolOutlined />
                    <span>{skill.name}</span>
                  </Tag>
                ))}
              </div>
            )}
            {!debugOnly && (
              <TextArea
                aria-label={`${copy.title}输出内容`}
                autoSize={{ minRows: 1, maxRows: 6 }}
                bordered={false}
                placeholder={copy.placeholder}
                value={draft}
                onChange={(event) => onDraftChange(event.target.value)}
                onKeyDown={handleInputKeyDown}
                onPressEnter={(event) => {
                  if (!event.shiftKey) {
                    event.preventDefault()
                    handleSend()
                  }
                }}
              />
            )}
          </div>
          {(debugEnabled || traceOpen) && (
            <div className={cx('workflow-debug-box', debugEnabled && 'enabled')}>
              {debugEnabled && (
                <div className={cx('workflow-debug-fields')}>
                  <Select
                    className={cx('workflow-debug-node-select')}
                    disabled={loading}
                    placeholder="选择开始节点"
                    value={resumeFrom}
                    onChange={setResumeFrom}
                  >
                    {resumeNodeOptions.map((option) => (
                      <Option key={option.value} value={option.value}>
                        {option.label}
                      </Option>
                    ))}
                  </Select>
                  <Text className={cx('workflow-debug-auto-paths')} title={workspaceRoot}>
                    自动读取当前工作目录下的 .xcodeagent 产物
                  </Text>
                  {resumeFrom === 'prepare_build_tasks' && (
                    <div className={cx('workflow-debug-build-scope')}>
                      <Select
                        className={cx('workflow-debug-scope-select')}
                        disabled={loading}
                        value={buildScopeType}
                        onChange={(value) =>
                          setBuildScopeType(value as WorkflowBuildExecutionScope['type'])
                        }
                      >
                        {buildScopeOptions.map((option) => (
                          <Option key={option.value} value={option.value}>
                            {option.label}
                          </Option>
                        ))}
                      </Select>
                      {buildScopeType !== 'application' && (
                        <Input
                          aria-label={buildScopeType === 'page' ? '页面 ID' : '数据源 ID'}
                          disabled={loading}
                          placeholder={
                            buildScopeType === 'page'
                              ? '输入 pageId，例如 orders'
                              : '输入 dataSourceId，例如 orders'
                          }
                          value={buildScopeTargetId}
                          onChange={(event) => setBuildScopeTargetId(event.target.value)}
                        />
                      )}
                      <Text className={cx('workflow-debug-scope-hint')}>
                        {buildScopeType === 'application'
                          ? '生成应用级分层 DAG'
                          : '只生成目标及其直接依赖的 DAG'}
                      </Text>
                    </div>
                  )}
                </div>
              )}
              {traceOpen && activeWorkflow && <WorkflowTraceLog workflow={activeWorkflow} />}
            </div>
          )}
          <div className={cx('ai-chat-composer-footer')}>
            {debugOnly ? (
              <Text className={cx('workflow-debug-resume-label')}>选择要重新开始执行的节点</Text>
            ) : (
              <div className={cx('composer-toolbar')}>
                <ResourceSkillMenu
                  disabled={loading || workspaceBusy}
                  onSelectedSkillsChange={onSelectedSkillsChange}
                  selectedSkills={selectedSkills}
                />
                <Tooltip
                  overlayClassName={cx('composer-tool-tooltip')}
                  title={debugEnabled ? '关闭 Workflow 调试' : 'Workflow 调试'}
                >
                  <Button
                    aria-label="Workflow 调试"
                    aria-pressed={debugEnabled}
                    className={cx('composer-tool-button', debugEnabled && 'active')}
                    disabled={loading}
                    icon={<BugOutlined />}
                    onClick={() => setDebugEnabled((current) => !current)}
                    shape="circle"
                    type="text"
                  />
                </Tooltip>
                <Tooltip
                  overlayClassName={cx('composer-tool-tooltip')}
                  title={traceOpen ? '收起 Trace 日志' : 'Trace 日志'}
                >
                  <Button
                    aria-expanded={traceOpen}
                    aria-label="Trace 日志"
                    className={cx('composer-tool-button', traceOpen && 'active')}
                    disabled={!activeWorkflow}
                    icon={<FileSearchOutlined />}
                    onClick={() => setTraceOpen((current) => !current)}
                    shape="circle"
                    type="text"
                  />
                </Tooltip>
              </div>
            )}
            {workspaceBusy && (
              <Text className={cx('workspace-busy-label')} type="warning">
                其他会话正在执行
              </Text>
            )}
            {loading ? (
              <Button
                aria-label={stopping ? '正在停止' : '停止生成'}
                className={cx('composer-send-button')}
                danger
                disabled={stopping}
                icon={<StopOutlined />}
                onClick={onStopGenerating}
                shape="circle"
                title={stopping ? '正在停止...' : '停止生成'}
              />
            ) : (
              <Button
                aria-label={debugEnabled ? '从指定节点执行' : '发送给 Workflow'}
                className={cx('composer-send-button')}
                disabled={!canSend || workspaceBusy}
                icon={<SendOutlined />}
                onClick={handleSend}
                shape="circle"
                title={debugEnabled ? '从指定节点执行' : '发送给 Workflow'}
                type="primary"
              />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function WorkflowTraceLog({ workflow }: { workflow: WorkflowRunPayload }): ReactElement {
  const observability = workflowObservability(workflow)
  return (
    <div className={cx('workflow-trace-log')}>
      <div className={cx('workflow-trace-meta')}>
        <Tag color={observability.langsmith.enabled ? 'green' : 'default'}>
          LangSmith {observability.langsmith.enabled ? '已开启' : '未开启'}
        </Tag>
        {observability.langsmith.project && <Text code>{observability.langsmith.project}</Text>}
        {observability.langsmith.traceSearchUrl && (
          <a href={observability.langsmith.traceSearchUrl} rel="noreferrer" target="_blank">
            打开 LangSmith
          </a>
        )}
      </div>
      <div className={cx('workflow-trace-events')}>
        {workflow.events.length > 0 ? (
          workflow.events.map((event, index) => (
            <div
              className={cx('workflow-trace-event')}
              key={`${event.type}-${event.timestamp}-${index}`}
            >
              <div className={cx('workflow-trace-event-line')}>
                <Tag>{event.nodeName || event.node?.id || event.type}</Tag>
                <Text code>{event.type}</Text>
                {event.timestamp && (
                  <Text className={cx('workflow-trace-time')} type="secondary">
                    {formatTraceTimestamp(event.timestamp)}
                  </Text>
                )}
                <Text>{event.message || event.status || event.type}</Text>
              </div>
              {hasTraceData(event.data) && (
                <pre className={cx('workflow-trace-data')}>{formatTraceData(event.data)}</pre>
              )}
            </div>
          ))
        ) : (
          <Text type="secondary">暂无 Workflow 事件</Text>
        )}
      </div>
    </div>
  )
}

function workflowObservability(workflow: WorkflowRunPayload): {
  langsmith: {
    enabled: boolean
    project: string
    traceSearchUrl: string
  }
} {
  const summaryObservability = objectValue(workflow.summary.observability)
  const stateObservability = objectValue(workflow.state?.observability)
  const eventObservability =
    workflow.events
      .map((event) => objectValue(event.data?.observability))
      .find((value) => Object.keys(value).length > 0) || {}
  const observability = firstRecord(summaryObservability, stateObservability, eventObservability)
  const langsmith = objectValue(observability.langsmith)
  return {
    langsmith: {
      enabled: Boolean(langsmith.enabled),
      project: stringValue(langsmith.project),
      traceSearchUrl: stringValue(langsmith.traceSearchUrl)
    }
  }
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function firstRecord(...values: Record<string, unknown>[]): Record<string, unknown> {
  return values.find((value) => Object.keys(value).length > 0) || {}
}

function hasTraceData(value: unknown): boolean {
  return Object.keys(objectValue(value)).length > 0
}

function formatTraceData(value: unknown): string {
  return JSON.stringify(value, null, 2)
}

function formatTraceTimestamp(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString()
}
