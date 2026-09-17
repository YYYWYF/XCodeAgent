import {
  ApiOutlined,
  LayoutOutlined,
  PaperClipOutlined,
  PartitionOutlined,
  PauseCircleOutlined,
  RightOutlined,
  SearchOutlined,
  SendOutlined,
  ToolOutlined
} from '@ant-design/icons'
import { Alert, Button, Empty, Input, Popover, Tag, Typography } from 'antd'
import type { KeyboardEvent, ReactElement } from 'react'
import { useMemo, useRef, useState } from 'react'
import type { WorkspaceSourceFile } from '../../../../mock/workspaceFiles'
import type { ChatMessageSkill, EditorMode } from '../../../../typings'
import { cx } from '../../../../utils'
import type { ComposerArtifactTarget, ComposerArtifactState } from '../../artifactMention'
import { skillsAfterEmptyBackspace } from '../../skillSelection'
import type { ChatCopy } from '../../types'
import ResourceSkillMenu from './ResourceSkillMenu'
import './ChatComposer.less'

const { Text } = Typography
const { TextArea } = Input

/** 产物状态徽标文案：对齐真实工程的产物状态口径（未开始/进行中/已完成）；continue 即行内「继续处理」按钮。 */
const ARTIFACT_STATE_LABELS: Record<ComposerArtifactState, string> = {
  continue: '继续处理',
  delivered: '已完成',
  'in-progress': '进行中',
  queued: '后台任务',
  ready: '未开始'
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
  onSend: (selectedFilePaths?: string[]) => Promise<void>
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
  workspaceBusy: boolean
}

export default function ChatComposer({
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
  workspaceBusy
}: ChatComposerProps): ReactElement {
  const [selectedFilePaths, setSelectedFilePaths] = useState<string[]>([])
  const [artifactPanelOpen, setArtifactPanelOpen] = useState(false)
  // 两级选择器当前选中的产物类别：左侧列选类别，右侧列列该类别下的产物。
  const [artifactCategory, setArtifactCategory] = useState<'app-api' | 'page'>('page')
  // 产物搜索词：真实应用会有几十个应用页面 / 应用API，靠名称或说明即时过滤才能快速定位。
  const [artifactSearch, setArtifactSearch] = useState('')
  const textAreaRef = useRef<HTMLTextAreaElement>(null)
  const frameRef = useRef<HTMLDivElement>(null)
  const artifactEnabled = Boolean(mentionItems && mentionItems.length > 0)
  const pageItems = useMemo(
    () => (mentionItems || []).filter((item) => item.kind === 'page'),
    [mentionItems]
  )
  const appApiItems = useMemo(
    () => (mentionItems || []).filter((item) => item.kind === 'app-api'),
    [mentionItems]
  )
  const activeArtifactItems = artifactCategory === 'page' ? pageItems : appApiItems
  // 按搜索词过滤当前类别的产物：名称与补充匹配文本（路由/字段数，不直接展示）都不区分大小写。
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
  const canSend = Boolean(draft.trim())

  /** 校验当前状态并提交对话内容。 */
  const handleSend = (): void => {
    // Agent 运行中允许继续编辑草稿，但不重复提交；真正不可用的只读态同样不能发送。
    if (loading || readOnly || workspaceBusy) return
    void onSend(selectedFilePaths)
      .then(() => {
        setSelectedFilePaths([])
      })
      .catch(() => undefined)
  }

  /** 切换两级选择器左列选中的产物类别；类别变化后旧搜索词不再适用，一并清空。 */
  const selectArtifactCategory = (kind: 'app-api' | 'page'): void => {
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
   * 行内只保留名称与状态徽标，形态对齐右侧产物目录；补充字段（路由、字段操作数）不进展示层。
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
      <span className={cx('composer-mention-copy')}>
        <Text ellipsis>{item.label}</Text>
      </span>
      <span className={cx('composer-mention-state', `is-${item.state}`)}>
        {ARTIFACT_STATE_LABELS[item.state]}
      </span>
    </button>
  )

  /** 两级选择器的左列项：类别名 + 数量，选中态高亮并带右箭头，与资源菜单同一套样式。 */
  const renderArtifactCategoryItem = (
    kind: 'app-api' | 'page',
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
            {renderArtifactCategoryItem('page', '应用页面', <LayoutOutlined />, pageItems.length)}
            {renderArtifactCategoryItem(
              'app-api',
              '应用API',
              <ApiOutlined />,
              appApiItems.length
            )}
          </div>
          <div className={cx('composer-artifact-secondary')}>
            {/* 搜索框需要自身可聚焦：阻断外层面板的 preventDefault，否则点不进输入框。 */}
            <div
              className={cx('composer-artifact-search')}
              onMouseDown={(event) => event.stopPropagation()}
            >
              <Input
                allowClear
                aria-label="搜索产物"
                bordered={false}
                placeholder={artifactCategory === 'page' ? '搜索应用页面名称 / 路径' : '搜索应用API'}
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
                        ? '暂无应用页面产物'
                        : '暂无应用API产物'
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
            {
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
            }
          </div>
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
                aria-label="发送给 Workflow"
                className={cx('composer-send-button')}
                disabled={!canSend || workspaceBusy || readOnly}
                icon={<SendOutlined />}
                onClick={handleSend}
                title="发送给 Workflow"
                type="primary"
              />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
