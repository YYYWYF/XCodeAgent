import {
  BugOutlined,
  FileSearchOutlined,
  FolderOpenOutlined,
  SendOutlined,
  StopOutlined
} from '@ant-design/icons'
import { Alert, Button, Checkbox, Input, Select, Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import { useState } from 'react'
import type { EditorMode, WorkflowDebugOptions, WorkflowRunPayload } from '../../../../typings'
import { cx } from '../../../../utils'
import type { ChatCopy } from '../../types'
import './ChatComposer.less'

const { Text } = Typography
const { TextArea } = Input
const { Option } = Select

const resumeNodeOptions = [
  { value: 'requirements', label: 'requirements' },
  { value: 'project_planning', label: 'project_planning' },
  { value: 'detail_confirmation', label: 'detail_confirmation' },
  { value: 'inspect_workspace', label: 'inspect_workspace' },
  { value: 'prepare_build_tasks', label: 'prepare_build_tasks' },
  { value: 'build', label: 'build' },
  { value: 'integration_test', label: 'integration_test' },
  { value: 'launch_project', label: 'launch_project' },
  { value: 'acceptance', label: 'acceptance' },
  { value: 'finalize_project', label: 'finalize_project' }
]

type ChatComposerProps = {
  activeWorkflow?: WorkflowRunPayload
  copy: ChatCopy[EditorMode]
  draft: string
  error?: string
  loading: boolean
  onDraftChange: (value: string) => void
  onSend: (workflowDebug?: WorkflowDebugOptions) => Promise<void>
  onStopGenerating: () => void
  stopping: boolean
  workspaceBusy: boolean
  workspaceRoot: string
}

export default function ChatComposer({
  activeWorkflow,
  copy,
  draft,
  error,
  loading,
  onDraftChange,
  onSend,
  onStopGenerating,
  stopping,
  workspaceBusy,
  workspaceRoot
}: ChatComposerProps): ReactElement {
  const [debugEnabled, setDebugEnabled] = useState(false)
  const [traceOpen, setTraceOpen] = useState(false)
  const [resumeFrom, setResumeFrom] = useState('requirements')
  const hasDebugNode = !debugEnabled || Boolean(resumeFrom)
  const canSend = debugEnabled ? hasDebugNode : Boolean(draft.trim())

  const currentDebugOptions = (): WorkflowDebugOptions | undefined =>
    debugEnabled
      ? {
          enabled: true,
          resumeFrom
        }
      : undefined

  const handleSend = (): void => {
    if (!hasDebugNode) return
    onSend(currentDebugOptions())
  }

  return (
    <div className={cx('ai-chat-composer')}>
      <div className={cx('ai-chat-composer-column')}>
        {error && <Alert message={error} showIcon type="error" />}
        <Text className={cx('composer-context-label')}>继续完善当前任务</Text>
        <div className={cx('ai-chat-composer-frame')}>
          <TextArea
            aria-label={`${copy.title}输出内容`}
            autoSize={{ minRows: 2, maxRows: 6 }}
            placeholder={copy.placeholder}
            value={draft}
            onChange={(event) => onDraftChange(event.target.value)}
            onPressEnter={(event) => {
              if (!event.shiftKey) {
                event.preventDefault()
                handleSend()
              }
            }}
          />
          <div className={cx('workflow-debug-box', debugEnabled && 'enabled')}>
            <div className={cx('workflow-debug-actions')}>
              <Checkbox
                checked={debugEnabled}
                disabled={loading}
                onChange={(event) => setDebugEnabled(event.target.checked)}
              >
                <BugOutlined /> Workflow 调试
              </Checkbox>
              <Button
                aria-expanded={traceOpen}
                disabled={!activeWorkflow}
                icon={<FileSearchOutlined />}
                onClick={() => setTraceOpen((current) => !current)}
                size="small"
                type="link"
              >
                {traceOpen ? '收起 Trace' : 'Trace 日志'}
              </Button>
            </div>
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
              </div>
            )}
            {traceOpen && activeWorkflow && (
              <WorkflowTraceLog workflow={activeWorkflow} />
            )}
          </div>
          <div className={cx('ai-chat-composer-footer')}>
            <Text className={cx('workspace-root-label')} title={workspaceRoot}>
              <FolderOpenOutlined /> {workspaceRoot}
            </Text>
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
            <div className={cx('workflow-trace-event')} key={`${event.type}-${event.timestamp}-${index}`}>
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
                <pre className={cx('workflow-trace-data')}>
                  {formatTraceData(event.data)}
                </pre>
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
  const eventObservability = workflow.events
    .map((event) => objectValue(event.data?.observability))
    .find((value) => Object.keys(value).length > 0) || {}
  const observability =
    firstRecord(summaryObservability, stateObservability, eventObservability)
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
    ? value as Record<string, unknown>
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
