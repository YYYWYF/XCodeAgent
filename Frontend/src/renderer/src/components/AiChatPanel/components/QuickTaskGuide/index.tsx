import {
  ApiOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  MessageOutlined,
  RightOutlined,
  RobotOutlined
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
import { buildQuickTasks, type QuickTaskItem } from './quickTasks'
import './QuickTaskGuide.less'
import './QuickTaskStatus.less'
import type { BusinessAgentOption } from '../../../../../../shared/businessAgent'
import { agentInvocation } from '../DocPanel/TechnicalPlanAgentContracts'

const { Text, Title } = Typography

type QuickTaskGuideProps = {
  agents?: BusinessAgentOption[]
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
    ) : type === 'agent' ? (
      <RobotOutlined />
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
            // 接口分类只投射调用信息，点击仍使用原 Agent 身份和正式开发门禁。
            const invocation =
              type === 'endpoint' && item.kind === 'agent'
                ? agentInvocation(item.agentId)
                : undefined
            const meta = invocation ? '智能体接口 · AG-UI / SSE' : item.meta
            const path = invocation
              ? `${invocation.method} ${invocation.path}`
              : item.kind === 'endpoint'
                ? item.endpointPath
                : undefined
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
                        {invocation ? <RobotOutlined /> : icon}
                      </span>
                      <span className={cx('quick-task-tooltip-kind')}>{title}</span>
                      <span className={cx('quick-task-tooltip-meta')}>{meta}</span>
                    </span>
                    <strong className={cx('quick-task-tooltip-title')}>{item.title}</strong>
                    {path ? <code>{path}</code> : null}
                    <span className={cx('quick-task-tooltip-description')}>{item.description}</span>
                  </span>
                }
              >
                <span className={cx('quick-task-tooltip-anchor')}>
                  <button
                    aria-label={`${item.title}，${meta}，${path || ''}，${item.description}，${developmentStatusText(item.progress)}`}
                    className={cx('quick-task-item', item.kind)}
                    disabled={disabled}
                    onClick={() => void onStart(item)}
                    type="button"
                  >
                    <span className={cx('quick-task-item-copy')}>
                      <span className={cx('quick-task-item-title-row')}>
                        <Text className={cx('quick-task-item-title')} strong>
                          {item.title}
                        </Text>
                        <Text className={cx('quick-task-item-meta')}>
                          {invocation ? <RobotOutlined /> : null} {meta}
                        </Text>
                      </span>
                      {path ? <code className={cx('quick-task-item-path')}>{path}</code> : null}
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
  agents = [],
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
    () => buildQuickTasks(pages, apiContracts, entities, developmentArtifacts, agents),
    [apiContracts, entities, pages, developmentArtifacts, agents]
  )
  const pageTasks = tasks.filter((task) => task.kind === 'page')
  const interfaceTasks = tasks.filter((task) => task.kind === 'endpoint' || task.kind === 'agent')

  return (
    <div className={cx('quick-task-guide')}>
      <header className={cx('quick-task-guide-heading')}>
        <span className={cx('quick-task-guide-mark')} aria-hidden="true">
          <MessageOutlined />
        </span>
        <div>
          <Title level={3}>今天想从哪里开始？</Title>
          <Text type="secondary">选择一个页面或接口开始正式任务，也可以直接在下方自由对话。</Text>
        </div>
      </header>

      {loading ? (
        <div className={cx('quick-task-loading')}>
          <Skeleton active paragraph={{ rows: 5 }} title={false} />
        </div>
      ) : (
        <div className={cx('quick-task-grid')}>
          {agents.length ? (
            <QuickTaskSection
              currentBranch={currentBranch}
              disabled={disabled}
              emptyText="暂无智能体。"
              items={tasks.filter((task) => task.kind === 'agent')}
              onStart={onStart}
              title="智能体"
              type="agent"
            />
          ) : null}
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
            items={interfaceTasks}
            onStart={onStart}
            title="接口"
            type="endpoint"
          />
        </div>
      )}
    </div>
  )
}
