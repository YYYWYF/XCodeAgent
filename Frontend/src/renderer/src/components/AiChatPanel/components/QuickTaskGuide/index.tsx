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
  DevelopmentPlanningApiContract,
  DevelopmentPlanningEntityOption,
  DevelopmentPlanningPageOption
} from '../../../../typings'
import { cx } from '../../../../utils'
import { buildQuickTasks, type QuickTaskItem } from './quickTasks'
import './QuickTaskGuide.less'

const { Text, Title } = Typography

type QuickTaskGuideProps = {
  apiContracts: DevelopmentPlanningApiContract[]
  disabled: boolean
  entities: DevelopmentPlanningEntityOption[]
  loading: boolean
  onStart: (task: QuickTaskItem) => Promise<void>
  pages: DevelopmentPlanningPageOption[]
}

type QuickTaskSectionProps = {
  disabled: boolean
  emptyText: string
  items: QuickTaskItem[]
  onStart: (task: QuickTaskItem) => Promise<void>
  title: string
  type: QuickTaskItem['kind']
}

/** 渲染一组会在通用历史会话中启动正式工作流的快捷任务。 */
function QuickTaskSection({
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
          {items.map((item) => (
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
                  <span className={cx('quick-task-tooltip-description')}>{item.description}</span>
                </span>
              }
            >
              <span className={cx('quick-task-tooltip-anchor')}>
                <button
                  aria-label={`${item.title}，${item.meta}，${item.description}`}
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
                      <Text className={cx('quick-task-item-meta')}>{item.meta}</Text>
                    </span>
                    <Text className={cx('quick-task-item-description')} type="secondary">
                      {item.description}
                    </Text>
                  </span>
                  <RightOutlined className={cx('quick-task-item-arrow')} />
                </button>
              </span>
            </Tooltip>
          ))}
        </div>
      ) : (
        <Text className={cx('quick-task-empty')} type="secondary">
          {emptyText}
        </Text>
      )}
    </section>
  )
}

/** 在空白对话区并排展示页面、Endpoint 与实体快捷任务，并保留底部自由输入入口。 */
export default function QuickTaskGuide({
  apiContracts,
  disabled,
  entities,
  loading,
  onStart,
  pages
}: QuickTaskGuideProps): ReactElement {
  const tasks = useMemo(
    () => buildQuickTasks(pages, apiContracts, entities),
    [apiContracts, entities, pages]
  )
  const pageTasks = tasks.filter((task) => task.kind === 'page')
  const endpointTasks = tasks.filter((task) => task.kind === 'endpoint')
  const entityTasks = tasks.filter((task) => task.kind === 'entity')

  return (
    <div className={cx('quick-task-guide')}>
      <header className={cx('quick-task-guide-heading')}>
        <span className={cx('quick-task-guide-mark')} aria-hidden="true">
          <MessageOutlined />
        </span>
        <div>
          <Title level={3}>今天想从哪里开始？</Title>
          <Text type="secondary">
            选择一个页面、Endpoint 或实体开始正式任务，也可以直接在下方自由对话。
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
            disabled={disabled}
            emptyText="项目计划中暂无页面。"
            items={pageTasks}
            onStart={onStart}
            title="页面"
            type="page"
          />
          <QuickTaskSection
            disabled={disabled}
            emptyText="项目计划中暂无 Endpoint。"
            items={endpointTasks}
            onStart={onStart}
            title="Endpoint"
            type="endpoint"
          />
          <QuickTaskSection
            disabled={disabled}
            emptyText="项目计划中暂无实体。"
            items={entityTasks}
            onStart={onStart}
            title="实体"
            type="entity"
          />
        </div>
      )}
    </div>
  )
}
