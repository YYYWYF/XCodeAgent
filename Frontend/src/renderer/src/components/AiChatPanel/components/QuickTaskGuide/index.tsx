import {
  ApiOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  MessageOutlined,
  RightOutlined
} from '@ant-design/icons'
import { Skeleton, Tooltip, Typography } from 'antd'
import type { ReactElement } from 'react'
import { useMemo } from 'react'
import type {
  DevelopmentArtifacts,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningEntityOption,
  DevelopmentPlanningPageOption
} from '../../../../typings'
import { cx } from '../../../../utils'
import { developmentDisplayStatus, developmentStatusText } from '../../../../developmentArtifacts'
import IterationBadge from '../../../IterationBadge'
import { buildQuickTasks, quickTaskBlocked, type QuickTaskItem } from './quickTasks'
import './QuickTaskGuide.less'
import './QuickTaskStatus.less'

const { Text, Title } = Typography

type QuickTaskGuideProps = {
  developmentArtifacts?: DevelopmentArtifacts
  /** 当前迭代的分支名（= 版本号），用于标注产物归属。 */
  currentBranch?: string
  apiContracts: DevelopmentPlanningApiContract[]
  disabled: boolean
  entities: DevelopmentPlanningEntityOption[]
  loading: boolean
  onStart: (task: QuickTaskItem) => Promise<void>
  pages: DevelopmentPlanningPageOption[]
}

type QuickTaskSectionProps = {
  currentBranch?: string
  disabled: boolean
  emptyText: string
  items: QuickTaskItem[]
  onStart: (task: QuickTaskItem) => Promise<void>
  title: string
  type: QuickTaskItem['kind']
}

/** 变更徽标文案：与计划阶段技术规划文档保持同一套说法。 */
const CHANGE_BADGE: Record<'added' | 'modified' | 'unchanged' | 'deleted', string> = {
  added: '新增',
  // 开发阶段统一叫"有变更"，与计划阶段的术语保持一致。
  modified: '有变更',
  unchanged: '未变更',
  deleted: '待删除'
}

/** 渲染一组会在通用历史会话中启动正式工作流的快捷任务。 */
function QuickTaskSection({
  currentBranch,
  disabled,
  emptyText,
  items,
  onStart,
  title,
  type
}: QuickTaskSectionProps): ReactElement {
  const icon =
    type === 'page' ? (
      <FileTextOutlined />
    ) : type === 'endpoint' ? (
      <ApiOutlined />
    ) : (
      <DatabaseOutlined />
    )
  return (
    <section className={cx('quick-task-section')}>
      <header className={cx('quick-task-section-header')}>
        <span className={cx('quick-task-section-icon')} aria-hidden="true">
          {icon}
        </span>
        <Text strong>{title}</Text>
        <Text type="secondary">{items.length}</Text>
      </header>
      {items.length > 0 ? (
        <div className={cx('quick-task-list')}>
          {items.map((item) => {
            // 已删除项与普通页面一样进入开发流程（由 workflow 删除代码与设计稿）；未变更项本轮无需重做，直接禁用。
            const deletion = item.changeStatus === 'deleted'
            const inherited = item.changeStatus === 'unchanged'
            const blocked = quickTaskBlocked(item)
            const hint = deletion
              ? '本轮已删除该页面，进入开发流程后会删除它的代码与 UI 设计稿。'
              : inherited
                ? '本轮未变更，该产物继承自上一版本，无需重新开发。'
                : item.description
            return (
            <Tooltip
              key={item.id}
              mouseEnterDelay={0.35}
              overlayClassName={cx('quick-task-tooltip')}
              placement="topLeft"
              title={
                <span className={cx('quick-task-tooltip-content')}>
                  <span className={cx('quick-task-tooltip-header')}>
                    <span className={cx('quick-task-tooltip-icon')} aria-hidden="true">
                      {icon}
                    </span>
                    <span className={cx('quick-task-tooltip-kind')}>{title}</span>
                    <span className={cx('quick-task-tooltip-meta')}>{item.meta}</span>
                  </span>
                  <strong className={cx('quick-task-tooltip-title')}>{item.title}</strong>
                  <span className={cx('quick-task-tooltip-description')}>{hint}</span>
                </span>
              }
            >
              <span className={cx('quick-task-tooltip-anchor')}>
                <button
                  aria-label={`${item.title}，${item.meta}，${item.description}，${developmentStatusText(item.progress)}`}
                  className={cx(
                    'quick-task-item',
                    item.kind,
                    deletion && 'is-deletion',
                    inherited && 'is-inherited'
                  )}
                  disabled={disabled || blocked}
                  onClick={() => void onStart(item)}
                  type="button"
                >
                  <span className={cx('quick-task-item-copy')}>
                    <span className={cx('quick-task-item-title-row')}>
                      <Text className={cx('quick-task-item-title')} strong>
                        {item.title}
                      </Text>
                      {/* 变更徽标固定在卡片右上角：与名称同行，但靠右对齐，不挤占名称。 */}
                      {item.changeStatus ? (
                        <span
                          className={cx('quick-task-item-change', `is-${item.changeStatus}`)}
                          title={`本轮${CHANGE_BADGE[item.changeStatus]}`}
                        >
                          {CHANGE_BADGE[item.changeStatus]}
                        </span>
                      ) : null}
                    </span>
                    {/* 页面路径移到名称下方单独一行，避免和徽标争同一行空间；
                        接口在这里展示 HTTP 方法与路径，同样与右上角徽标错开。 */}
                    {item.kind === 'page' ? (
                      <code className={cx('quick-task-item-path')}>{item.meta}</code>
                    ) : null}
                    {item.kind === 'endpoint' ? (
                      <span className={cx('quick-task-item-endpoint-line')}>
                        <em className={cx('quick-task-item-meta')}>{item.meta}</em>
                        <code className={cx('quick-task-item-path')}>{item.endpointPath}</code>
                      </span>
                    ) : null}
                    <Text className={cx('quick-task-item-description')} type="secondary">
                      {item.description}
                    </Text>
                    <span className={cx('quick-task-item-status-row')}>
                      <span
                        className={cx('quick-task-item-status')}
                        data-status={developmentDisplayStatus(item.progress)}
                      >
                        {developmentStatusText(item.progress)}
                      </span>
                      {/* 归属标注：这个产物是以前迭代做的还是本轮的。
                          归属未知时不渲染（组件自己处理）。 */}
                      <IterationBadge
                        artifactBranch={item.progress?.completedBranchName}
                        currentBranch={currentBranch}
                      />
                    </span>
                  </span>
                  <RightOutlined className={cx('quick-task-item-arrow')} />
                </button>
              </span>
            </Tooltip>
            )
          })}
        </div>
      ) : (
        <Text className={cx('quick-task-empty')} type="secondary">
          {emptyText}
        </Text>
      )}
    </section>
  )
}

/** 在空白对话区并排展示页面与接口快捷任务，并保留底部自由输入入口。 */
export default function QuickTaskGuide({
  developmentArtifacts,
  currentBranch,
  apiContracts,
  disabled,
  entities,
  loading,
  onStart,
  pages
}: QuickTaskGuideProps): ReactElement {
  const tasks = useMemo(
    () => buildQuickTasks(pages, apiContracts, entities, developmentArtifacts),
    [apiContracts, entities, pages, developmentArtifacts]
  )
  const pageTasks = tasks.filter((task) => task.kind === 'page')
  const endpointTasks = tasks.filter((task) => task.kind === 'endpoint')

  return (
    <div className={cx('quick-task-guide')}>
      <header className={cx('quick-task-guide-heading')}>
        <span className={cx('quick-task-guide-mark')} aria-hidden="true">
          <MessageOutlined />
        </span>
        <div>
          <Title level={3}>今天想从哪里开始？</Title>
          <Text type="secondary">
            选择一个页面或接口开始正式任务，也可以直接在下方自由对话。
          </Text>
        </div>
      </header>

      {loading ? (
        <div className={cx('quick-task-loading')}>
          <Skeleton active paragraph={{ rows: 5 }} title={false} />
        </div>
      ) : (
        <div className={cx('quick-task-grid')}>
          <QuickTaskSection
            currentBranch={currentBranch}
            disabled={disabled}
            emptyText="项目计划中暂无页面。"
            items={pageTasks}
            onStart={onStart}
            title="页面"
            type="page"
          />
          <QuickTaskSection
            currentBranch={currentBranch}
            disabled={disabled}
            emptyText="项目计划中暂无接口。"
            items={endpointTasks}
            onStart={onStart}
            title="接口"
            type="endpoint"
          />

        </div>
      )}
    </div>
  )
}
