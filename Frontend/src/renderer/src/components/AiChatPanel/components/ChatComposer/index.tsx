import {
  BugOutlined,
  FileSearchOutlined,
  SendOutlined,
  StopOutlined,
  ToolOutlined
} from '@ant-design/icons'
import { Button, Input, Select, Switch, Tag, Tooltip, Typography } from 'antd'
import type { KeyboardEvent, ReactElement } from 'react'
import { useEffect, useState } from 'react'
import type {
  ChatMessageSkill,
  EditorMode,
  WorkflowBuildExecutionScope,
  WorkflowDebugOptions,
  WorkflowDebugStage,
  WorkflowRunPayload,
  InspectedElementContext
} from '../../../../typings'
import { cx } from '../../../../utils'
import { skillsAfterEmptyBackspace } from '../../skillSelection'
import type { ChatCopy } from '../../types'
import { workflowDebugNodesForStage } from '../../../../service/workflowDebugNodes'
import ResourceSkillMenu from './ResourceSkillMenu'
import { workflowDebugBuildScope } from '../../debugExecutionScope'
import { productDesignStageMentionFromText } from './productConversation'
import WorkflowStageMentionInput from './WorkflowStageMentionInput'
import './ChatComposer.less'

const { Text } = Typography
const { TextArea } = Input
const { Option } = Select

const buildScopeOptions: Array<{ value: WorkflowBuildExecutionScope['type']; label: string }> = [
  { value: 'application', label: '整个应用' },
  { value: 'page', label: '单个页面' },
  { value: 'endpoint', label: '单个接口' }
]

type ChatComposerProps = {
  activeWorkflow?: WorkflowRunPayload
  copy: ChatCopy[EditorMode]
  debugOnly?: boolean
  debugStage?: WorkflowDebugStage
  draft: string
  initialResumeFrom?: string
  inspectedElementContext?: InspectedElementContext
  loading: boolean
  onDraftChange: (value: string) => void
  onInspectedElementContextClear?: () => void
  onSelectedSkillsChange: (skills: ChatMessageSkill[]) => void
  onSend: (
    workflowDebug?: WorkflowDebugOptions,
    confirmScopeBeforeExecution?: boolean
  ) => Promise<void>
  onStopGenerating: () => void
  placeholder?: string
  showScopeConfirmationToggle?: boolean
  initialScopeConfirmationEnabled?: boolean
  sendDisabled?: boolean
  sendDisabledHint?: string
  stopping: boolean
  selectedSkills: ChatMessageSkill[]
  workspaceBusy: boolean
  workspaceRoot: string
}

/** 按当前工作阶段展示专属调试节点，并处理工作流输入与节点启动参数。 */
export default function ChatComposer({
  activeWorkflow,
  copy,
  debugOnly = false,
  debugStage = 'development',
  draft,
  initialResumeFrom = 'api_design_readiness_gate',
  inspectedElementContext,
  loading,
  onDraftChange,
  onInspectedElementContextClear,
  onSelectedSkillsChange,
  onSend,
  onStopGenerating,
  placeholder,
  showScopeConfirmationToggle = false,
  initialScopeConfirmationEnabled = true,
  sendDisabled = false,
  sendDisabledHint,
  stopping,
  selectedSkills,
  workspaceBusy,
  workspaceRoot
}: ChatComposerProps): ReactElement {
  const initialBuildScope = workflowDebugBuildScope(activeWorkflow)
  const resumeNodeOptions = workflowDebugNodesForStage(debugStage)
  const [debugEnabled, setDebugEnabled] = useState(debugOnly)
  const [confirmScopeBeforeExecution, setConfirmScopeBeforeExecution] = useState(
    initialScopeConfirmationEnabled
  )
  const [traceOpen, setTraceOpen] = useState(false)
  const [resumeFrom, setResumeFrom] = useState(initialResumeFrom)
  const [buildScopeType, setBuildScopeType] =
    useState<WorkflowBuildExecutionScope['type']>(initialBuildScope.type)
  const [buildScopeTargetId, setBuildScopeTargetId] = useState(
    initialBuildScope.targetId || ''
  )
  const [buildScopeApiContractId, setBuildScopeApiContractId] = useState(
    initialBuildScope.apiContractId || ''
  )
  // 从待处理的二次修改澄清或确认卡恢复本轮开关状态。
  useEffect(() => {
    if (typeof initialScopeConfirmationEnabled === 'boolean') {
      setConfirmScopeBeforeExecution(initialScopeConfirmationEnabled)
    }
  }, [initialScopeConfirmationEnabled])
  const selectedResumeFrom = resumeNodeOptions.some((option) => option.value === resumeFrom)
    ? resumeFrom
    : resumeNodeOptions[0]?.value || ''
  const hasDebugNode = !debugEnabled || Boolean(selectedResumeFrom)
  const isBuildTaskDebug = debugEnabled && selectedResumeFrom === 'prepare_build_tasks'
  const hasBuildScopeTarget =
    buildScopeType === 'application' ||
    (Boolean(buildScopeTargetId.trim()) &&
      (buildScopeType !== 'endpoint' || Boolean(buildScopeApiContractId.trim())))
  const canSend = debugEnabled
    ? hasDebugNode && (!isBuildTaskDebug || hasBuildScopeTarget)
    : Boolean(draft.trim())

  /** 根据调试开关生成 Workflow 调试参数，并在任务拆分节点附带分层 DAG 范围。 */
  const currentDebugOptions = (): WorkflowDebugOptions | undefined =>
    debugEnabled
      ? {
          enabled: true,
          resumeFrom: selectedResumeFrom,
          ...(isBuildTaskDebug
            ? {
                buildExecutionScope: {
                  type: buildScopeType,
                  ...(buildScopeType === 'application'
                    ? {}
                    : {
                        targetId: buildScopeTargetId.trim(),
                        ...(buildScopeType === 'endpoint'
                          ? { apiContractId: buildScopeApiContractId.trim() }
                          : {})
                      })
                }
              }
            : {})
        }
      : undefined

  /** 校验当前状态并提交对话内容。 */
  const handleSend = (): void => {
    if (!hasDebugNode || workspaceBusy || sendDisabled || (debugOnly && loading)) return
    // 计划阶段的 @ 是对话意图提示，保留原文交给技术规划路由，不伪装成调试重入。
    const stageMention = debugStage === 'design'
      ? productDesignStageMentionFromText(draft, debugStage) : undefined
    onSend(
      stageMention
        ? { enabled: true, resumeFrom: stageMention.node }
        : debugStage === 'planning' && productDesignStageMentionFromText(draft, debugStage)
          ? undefined
          : currentDebugOptions(),
      confirmScopeBeforeExecution
    )
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
              debugStage === 'design' || debugStage === 'planning' ? (
                <WorkflowStageMentionInput
                  key={debugStage}
                  stage={debugStage}
                  ariaLabel={`${copy.title}输出内容`}
                  onChange={onDraftChange}
                  onKeyDown={handleInputKeyDown}
                  onPressEnter={(event) => {
                    if (!event.shiftKey) {
                      event.preventDefault()
                      handleSend()
                    }
                  }}
                  placeholder={placeholder || copy.placeholder}
                  value={draft}
                />
              ) : (
                <TextArea
                  aria-label={`${copy.title}输出内容`}
                  autoSize={{ minRows: 1, maxRows: 6 }}
                  bordered={false}
                  placeholder={placeholder || copy.placeholder}
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
              )
            )}
          </div>
          {!debugOnly && inspectedElementContext && (
            <div className={cx('composer-selected-element')}>
              <Tooltip
                overlayClassName={cx('composer-tool-tooltip')}
                title={`${inspectedElementContext.sourcePath}:${inspectedElementContext.line}:${inspectedElementContext.column}`}
              >
                <Tag closable={!loading} onClose={onInspectedElementContextClear}>
                  已选择 {inspectedElementContext.tagName}
                </Tag>
              </Tooltip>
            </div>
          )}
          {(debugEnabled || traceOpen) && (
            <div className={cx('workflow-debug-box', debugEnabled && 'enabled')}>
              {debugEnabled && (
                <div className={cx('workflow-debug-fields')}>
                  <Select
                    className={cx('workflow-debug-node-select')}
                    disabled={loading}
                    dropdownClassName={cx('workflow-debug-node-dropdown')}
                    optionLabelProp="label"
                    placeholder="选择开始节点"
                    value={selectedResumeFrom}
                    onChange={setResumeFrom}
                  >
                    {resumeNodeOptions.map((option) => (
                      <Option
                        key={option.value}
                        label={
                          <span className={cx('workflow-debug-node-option')}>
                            <span className={cx('workflow-debug-node-title')}>
                              {option.title}
                            </span>
                            <span className={cx('workflow-debug-node-id')}>
                              {option.value}
                            </span>
                          </span>
                        }
                        value={option.value}
                      >
                        <span className={cx('workflow-debug-node-option')}>
                          <span className={cx('workflow-debug-node-title')}>
                            {option.title}
                          </span>
                          <span className={cx('workflow-debug-node-id')}>
                            {option.value}
                          </span>
                        </span>
                      </Option>
                    ))}
                  </Select>
                  <Text className={cx('workflow-debug-auto-paths')} title={workspaceRoot}>
                    自动读取当前工作目录下的 .devagentstudio 产物
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
                          aria-label={
                            buildScopeType === 'page'
                              ? '页面 ID'
                              : buildScopeType === 'endpoint'
                                ? '接口 ID'
                                : '数据源 ID'
                          }
                          disabled={loading}
                          placeholder={
                            buildScopeType === 'page'
                              ? '输入 pageId，例如 orders'
                              : buildScopeType === 'endpoint'
                                ? '输入 endpointId，例如 orders.list'
                                : '输入 dataSourceId，例如 orders'
                          }
                          value={buildScopeTargetId}
                          onChange={(event) => setBuildScopeTargetId(event.target.value)}
                        />
                      )}
                      {buildScopeType === 'endpoint' && (
                        <Input
                          aria-label="API Contract ID"
                          disabled={loading}
                          placeholder="输入 apiContractId，例如 orders-api"
                          value={buildScopeApiContractId}
                          onChange={(event) => setBuildScopeApiContractId(event.target.value)}
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
            {workspaceBusy && !(debugOnly && loading) && (
              <Text className={cx('workspace-busy-label')} type="warning">
                其他会话正在执行
              </Text>
            )}
            {!workspaceBusy && sendDisabled && sendDisabledHint && (
              <Text className={cx('workspace-busy-label')} type="warning">
                {sendDisabledHint}
              </Text>
            )}
            {debugOnly && loading && (
              <Text className={cx('workflow-debug-running-hint')}>
                {stopping ? 'Agent 正在退出，请稍候' : 'Agent 正在运行，结束任务后可调试'}
              </Text>
            )}
            <div className={cx('composer-send-actions')}>
              {showScopeConfirmationToggle && !debugEnabled && (
                <Tooltip
                  overlayClassName={cx('composer-tool-tooltip')}
                  title="开启后会先整理澄清后的需求和修改范围，确认后才开始改代码；关闭时只在需求不明确时追问。"
                >
                  <label className={cx('composer-scope-confirmation')}>
                    <Switch
                      aria-label="先确认修改范围"
                      checked={confirmScopeBeforeExecution}
                      disabled={loading || workspaceBusy || sendDisabled}
                      onChange={setConfirmScopeBeforeExecution}
                      size="small"
                    />
                    <Text>先澄清后开发</Text>
                  </label>
                </Tooltip>
              )}
              {(loading || stopping) && !debugOnly ? (
                <Button
                  aria-label={stopping ? '正在停止生成' : '停止生成'}
                  aria-busy={stopping}
                  className={cx('composer-send-button')}
                  danger
                  disabled={stopping}
                  loading={stopping}
                  icon={<StopOutlined />}
                  onClick={onStopGenerating}
                  shape="circle"
                  title={stopping ? '正在停止生成…' : '停止生成'}
                />
              ) : (
                <Button
                  aria-label={debugEnabled ? '从指定节点执行' : '发送给 Workflow'}
                  className={cx('composer-send-button')}
                  disabled={!canSend || workspaceBusy || sendDisabled || (debugOnly && loading)}
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
