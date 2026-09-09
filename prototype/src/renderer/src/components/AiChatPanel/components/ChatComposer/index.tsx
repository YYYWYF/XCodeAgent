import {
  ApiOutlined,
  BugOutlined,
  FileSearchOutlined,
  LayoutOutlined,
  PaperClipOutlined,
  PartitionOutlined,
  PauseCircleOutlined,
  RightOutlined,
  SearchOutlined,
  SendOutlined,
  ToolOutlined
} from '@ant-design/icons'
import { Alert, Button, Empty, Input, Popover, Select, Tag, Tooltip, Typography } from 'antd'
import type { KeyboardEvent, ReactElement } from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { WorkspaceSourceFile } from '../../../../mock/workspaceFiles'
import type {
  ChatMessageSkill,
  EditorMode,
  WorkflowBuildExecutionScope,
  WorkflowDebugOptions,
  WorkflowRunPayload
} from '../../../../typings'
import { cx } from '../../../../utils'
import type { ComposerArtifactTarget, ComposerArtifactState } from '../../artifactMention'
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
  { value: 'inspect_database_context', label: 'inspect_database_context' },
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

/** 产物状态徽标文案：让“能不能发起”直接可见，而不是点失败了才知道；continue 即行内「继续处理」按钮。 */
const ARTIFACT_STATE_LABELS: Record<ComposerArtifactState, string> = {
  continue: '继续处理',
  delivered: '已交付',
  'in-progress': '实施中',
  queued: '后台任务',
  ready: '待实施'
}

/** 可从输入区恢复的后台工作流：仅暴露用户识别与继续所需的最小信息。 */
export type PendingWorkflowContinuation = {
  taskId: string
  title: string
}

/** 把候选弹层挂到触发按钮的父级容器内：与资源菜单一致，浅色变量与层级都跟随输入区本地上下文。 */
function getMentionPopupContainer(triggerNode: HTMLElement): HTMLElement {
  return triggerNode.parentElement || triggerNode
}

type ChatComposerProps = {
  activeWorkflow?: WorkflowRunPayload
  availableFiles: WorkspaceSourceFile[]
  copy: ChatCopy[EditorMode]
  draft: string
  error?: string
  loading: boolean
  onDraftChange: (value: string) => void
  /** 从产物面板直接发起某产物的实施 Workflow（不产生用户消息输入）。 */
  onLaunchArtifact: (item: ComposerArtifactTarget) => void
  /** 从输入区恢复一条后台完成后待处理的工作流。 */
  onResumePendingWorkflow?: (taskId: string) => void
  onSend: (workflowDebug?: WorkflowDebugOptions, selectedFilePaths?: string[]) => Promise<void>
  onSelectedSkillsChange: (skills: ChatMessageSkill[]) => void
  onStopGenerating: () => void
  /** 覆盖默认占位文案（开发阶段提示「产物」按钮发起）。 */
  placeholder?: string
  readOnly?: boolean
  readOnlyMessage?: string
  stopping: boolean
  selectedSkills: ChatMessageSkill[]
  /** 可发起的开发产物清单；不传表示当前阶段不开放产物发起。 */
  mentionItems?: ComposerArtifactTarget[]
  /** 后台任务完成但尚未继续的工作流；存在时输入工具栏展示提醒圆点。 */
  pendingWorkflowContinuations?: PendingWorkflowContinuation[]
  /** 引导消息快速按钮的打开信号：递增时远程展开产物选择弹层（与工作流按钮同一弹层）。 */
  artifactPickerRequest?: number
  workspaceBusy: boolean
  workspaceRoot: string
}

export default function ChatComposer({
  activeWorkflow,
  availableFiles,
  copy,
  draft,
  error,
  loading,
  onDraftChange,
  onLaunchArtifact,
  onResumePendingWorkflow,
  onSend,
  onSelectedSkillsChange,
  onStopGenerating,
  placeholder,
  readOnly = false,
  readOnlyMessage = '当前内容只读',
  stopping,
  selectedSkills,
  mentionItems,
  pendingWorkflowContinuations = [],
  artifactPickerRequest,
  workspaceBusy,
  workspaceRoot
}: ChatComposerProps): ReactElement {
  const [debugEnabled, setDebugEnabled] = useState(false)
  const [traceOpen, setTraceOpen] = useState(false)
  const [resumeFrom, setResumeFrom] = useState('detail_confirmation')
  const [buildScopeType, setBuildScopeType] =
    useState<WorkflowBuildExecutionScope['type']>('application')
  const [buildScopeTargetId, setBuildScopeTargetId] = useState('')
  const [selectedFilePaths, setSelectedFilePaths] = useState<string[]>([])
  const [artifactPanelOpen, setArtifactPanelOpen] = useState(false)
  // 引导消息快速按钮的打开信号：请求号递增即展开产物选择弹层。
  useEffect(() => {
    if (!artifactPickerRequest) return
    setArtifactPanelOpen(true)
  }, [artifactPickerRequest])
  // 两级选择器当前选中的产物类别：左侧列选类别，右侧列列该类别下的产物。
  const [artifactCategory, setArtifactCategory] = useState<'endpoint' | 'page'>('page')
  // 产物搜索词：真实应用会有几十个页面 / 接口，靠名称或路径/契约即时过滤才能快速定位。
  const [artifactSearch, setArtifactSearch] = useState('')
  const textAreaRef = useRef<HTMLTextAreaElement>(null)
  const frameRef = useRef<HTMLDivElement>(null)
  const artifactEnabled = Boolean(mentionItems && mentionItems.length > 0)
  const pageItems = useMemo(
    () => (mentionItems || []).filter((item) => item.kind === 'page'),
    [mentionItems]
  )
  const endpointItems = useMemo(
    () => (mentionItems || []).filter((item) => item.kind === 'endpoint'),
    [mentionItems]
  )
  const activeArtifactItems = artifactCategory === 'page' ? pageItems : endpointItems
  // 按搜索词过滤当前类别的产物：名称与第二行提示（页面路径 / 接口契约）都不区分大小写匹配。
  const visibleArtifactItems = useMemo(() => {
    const keyword = artifactSearch.trim().toLowerCase()
    if (!keyword) return activeArtifactItems
    return activeArtifactItems.filter(
      (item) =>
        item.label.toLowerCase().includes(keyword) || item.hint.toLowerCase().includes(keyword)
    )
  }, [activeArtifactItems, artifactSearch])
  const pendingContinuationCount = pendingWorkflowContinuations.length
  const continuationActionDisabled = workspaceBusy || readOnly
  const workflowEntryEnabled = artifactEnabled || pendingContinuationCount > 0

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
    // Agent 运行中允许继续编辑草稿，但不重复提交；真正不可用的只读态同样不能发送。
    if (!hasDebugNode || loading || readOnly || workspaceBusy) return
    void onSend(currentDebugOptions(), selectedFilePaths).then(() => {
      setSelectedFilePaths([])
    }).catch(() => undefined)
  }

  /** 切换两级选择器左列选中的产物类别；类别变化后旧搜索词不再适用，一并清空。 */
  const selectArtifactCategory = (kind: 'endpoint' | 'page'): void => {
    setArtifactCategory(kind)
    setArtifactSearch('')
  }

  /** 关闭产物面板时清空搜索词，下次打开回到完整列表。 */
  const handleArtifactPanelClose = (open: boolean): void => {
    setArtifactPanelOpen(open)
    if (!open) setArtifactSearch('')
  }

  /**
   * 产物树的单个节点：状态徽标直观呈现可否发起；「继续处理」行点击恢复该产物已完成的工作流，
   * 其余可用节点点击直接发起实施；禁用节点点击后由发起校验给出原因反馈。
   */
  const renderArtifactNode = (item: ComposerArtifactTarget): ReactElement => (
    <button
      aria-disabled={item.disabled}
      className={cx('composer-mention-item', item.disabled && 'disabled')}
      key={item.artifactId}
      onClick={() => {
        setArtifactPanelOpen(false)
        if (item.continuationTaskId) {
          onResumePendingWorkflow?.(item.continuationTaskId)
          return
        }
        onLaunchArtifact(item)
      }}
      role="treeitem"
      title={
        item.disabled
          ? item.disabledReason
          : item.continuationTaskId
            ? '该产物的实现已完成，点击继续处理后续工作流'
            : '点击直接发起实施'
      }
      type="button"
    >
      <span className={cx('composer-mention-icon')}>
        {item.kind === 'page' ? <LayoutOutlined /> : <ApiOutlined />}
      </span>
      <span className={cx('composer-mention-copy')}>
        <Text>{item.label}</Text>
        <Text type="secondary">{item.hint}</Text>
      </span>
      <span className={cx('composer-mention-state', `is-${item.state}`)}>
        {ARTIFACT_STATE_LABELS[item.state]}
      </span>
    </button>
  )

  /** 两级选择器的左列项：类别名 + 数量，选中态高亮并带右箭头，与资源菜单同一套样式。 */
  const renderArtifactCategoryItem = (
    kind: 'endpoint' | 'page',
    title: string,
    icon: ReactElement,
    count: number
  ): ReactElement => (
    <button
      aria-selected={artifactCategory === kind}
      className={cx('composer-resource-item', artifactCategory === kind && 'active')}
      key={kind}
      onClick={() => selectArtifactCategory(kind)}
      type="button"
    >
      {icon}
      <span>{title}</span>
      <small>{count}</small>
      <RightOutlined />
    </button>
  )

  /**
   * 产物面板内容：两级选择器——左列选类别，右列搜索 + 列出该类别下的产物并带状态徽标。
   * 待继续工作流不单独成表：对应产物行直接呈现为「继续处理」按钮，与发起新实施共用一张列表；
   * 仅当前阶段没有产物面板可承载时（如规划产物为空），才退回独立待继续列表兜底。
   */
  const artifactPanelContent = (
    <div
      aria-label="工作流"
      className={cx('composer-workflow-popover')}
      // 阻止面板抢走输入框焦点：点击选择时 TextArea 不失焦，输入内容不丢失。
      onMouseDown={(event) => event.preventDefault()}
    >
      {!artifactEnabled && pendingContinuationCount > 0 ? (
        <div aria-label="待继续工作流" className={cx('composer-continuation-popover')}>
          <div className={cx('composer-continuation-header')}>
            <Text strong>待继续工作流</Text>
            <Text type="secondary">{pendingContinuationCount} 项</Text>
          </div>
          <div className={cx('composer-continuation-list')}>
            {pendingWorkflowContinuations.map((continuation) => (
              <button
                disabled={continuationActionDisabled}
                key={continuation.taskId}
                onClick={() => {
                  setArtifactPanelOpen(false)
                  onResumePendingWorkflow?.(continuation.taskId)
                }}
                type="button"
              >
                <span>{continuation.title}</span>
                <small>继续处理</small>
              </button>
            ))}
          </div>
        </div>
      ) : null}
      {artifactEnabled ? (
        <div aria-label="产物选择" className={cx('composer-artifact-popover')} role="tree">
          <div className={cx('composer-resource-primary')}>
            {renderArtifactCategoryItem('page', '页面', <LayoutOutlined />, pageItems.length)}
            {renderArtifactCategoryItem('endpoint', '接口', <ApiOutlined />, endpointItems.length)}
          </div>
          <div className={cx('composer-artifact-secondary')}>
            {/* 搜索框需要自身可聚焦：阻断外层面板的 preventDefault，否则点不进输入框。 */}
            {/* eslint-disable-next-line jsx-a11y/no-static-element-interactions */}
            <div
              className={cx('composer-artifact-search')}
              onMouseDown={(event) => event.stopPropagation()}
            >
              <Input
                allowClear
                aria-label="搜索产物"
                bordered={false}
                placeholder={
                  artifactCategory === 'page' ? '搜索页面名称 / 路径' : '搜索接口 / 契约'
                }
                prefix={<SearchOutlined />}
                value={artifactSearch}
                onChange={(event) => setArtifactSearch(event.target.value)}
              />
            </div>
            <div className={cx('composer-mention-panel')} role="tree">
              {visibleArtifactItems.length > 0 ? (
                visibleArtifactItems.map((item) => renderArtifactNode(item))
              ) : (
                <Empty
                  description={
                    artifactSearch.trim()
                      ? '未找到匹配的产物'
                      : artifactCategory === 'page'
                        ? '暂无页面产物'
                        : '暂无接口产物'
                  }
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                />
              )}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  )

  /** 处理输入区键盘操作：回车发送、技能标签 Backspace 删除。 */
  const handleInputKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>): void => {
    // Enter 发送：产物面板由按钮独立驱动，不再劫持输入框按键。
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      handleSend()
      return
    }
    const nextSkills = skillsAfterEmptyBackspace(event.key, draft, selectedSkills)
    if (!nextSkills) return
    event.preventDefault()
    onSelectedSkillsChange(nextSkills)
  }

  return (
    <div className={cx('ai-chat-composer')}>
      <div className={cx('ai-chat-composer-column')}>
        {error && <Alert message={error} showIcon type="error" />}
        <div
          ref={frameRef}
          aria-label="对话输入区"
          className={cx(
            'ai-chat-composer-frame',
            readOnly && 'is-disabled',
            loading && !readOnly && 'is-loading'
          )}
        >
          <div className={cx('composer-inline-input')}>
            {selectedSkills.length > 0 && (
              <div className={cx('composer-selected-skills')}>
                {selectedSkills.map((skill) => (
                  <Tag
                    closable={!readOnly}
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
            {selectedFilePaths.length > 0 && (
              <div className={cx('composer-selected-files')}>
                {selectedFilePaths.map((path) => (
                  <Tag
                    closable={!readOnly}
                    key={path}
                    onClose={() =>
                      setSelectedFilePaths((current) => current.filter((item) => item !== path))
                    }
                    title={path}
                  >
                    <PaperClipOutlined />
                    <span>{path.split('/').pop() || path}</span>
                  </Tag>
                ))}
              </div>
            )}
            {(
              <TextArea
                aria-label={`${copy.title}输出内容`}
                autoSize={{ minRows: 1, maxRows: 6 }}
                bordered={false}
                readOnly={readOnly}
                placeholder={placeholder ?? copy.placeholder}
                ref={textAreaRef}
                value={draft}
                onChange={(event) => onDraftChange(event.target.value)}
                onKeyDown={handleInputKeyDown}
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
            <div className={cx('composer-toolbar')}>
                <ResourceSkillMenu
                  availableFiles={availableFiles}
                  disabled={workspaceBusy || readOnly}
                  onSelectedFilePathsChange={setSelectedFilePaths}
                  onSelectedSkillsChange={onSelectedSkillsChange}
                  selectedFilePaths={selectedFilePaths}
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
                    disabled={loading || readOnly}
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
                {/* 工作流入口统一承载产物发起与待继续任务，避免为同一类流程再增加第二个图标。 */}
                {workflowEntryEnabled ? (
                  <Popover
                    content={artifactPanelContent}
                    getPopupContainer={getMentionPopupContainer}
                    overlayClassName={cx('composer-mention-overlay')}
                    placement="topLeft"
                    trigger="click"
                    visible={artifactPanelOpen}
                    onVisibleChange={handleArtifactPanelClose}
                  >
                    <span className={cx('composer-workflow-trigger')}>
                      {pendingContinuationCount > 0 ? (
                        <span aria-hidden="true" className={cx('composer-workflow-reminder')} />
                      ) : null}
                      <Button
                        aria-expanded={artifactPanelOpen}
                        aria-haspopup="dialog"
                        aria-label={
                          pendingContinuationCount > 0
                            ? `工作流，${pendingContinuationCount} 项待继续`
                            : '工作流'
                        }
                        className={cx('composer-tool-button', artifactPanelOpen && 'active')}
                        disabled={workspaceBusy || readOnly}
                        icon={<PartitionOutlined />}
                        shape="circle"
                        title={
                          pendingContinuationCount > 0
                            ? `工作流（${pendingContinuationCount} 项待继续）`
                            : '选择产物并直接发起实施'
                        }
                        type="text"
                      />
                    </span>
                  </Popover>
                ) : null}
              </div>
            {workspaceBusy && !readOnly && (
              <Text className={cx('workspace-busy-label')} type="warning">
                当前 Workflow 正在执行
              </Text>
            )}
            {readOnly ? (
              <Text className={cx('workspace-busy-label')} type="secondary">
                {readOnlyMessage}
              </Text>
            ) : null}
            {loading && !readOnly ? (
              <Button
                aria-label={stopping ? '正在停止' : '停止生成'}
                className={cx('composer-send-button', 'is-abort')}
                danger
                disabled={stopping}
                icon={<PauseCircleOutlined />}
                onClick={onStopGenerating}
                title={stopping ? '正在停止...' : '停止生成'}
              />
            ) : readOnly ? (
              <Button
                aria-label="对话已锁定"
                className={cx('composer-send-button')}
                disabled
                icon={<SendOutlined />}
                title={readOnlyMessage}
              />
            ) : (
              <Button
                aria-label={debugEnabled ? '从指定节点执行' : '发送给 Workflow'}
                className={cx('composer-send-button')}
                disabled={!canSend || workspaceBusy || readOnly}
                icon={<SendOutlined />}
                onClick={handleSend}
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
