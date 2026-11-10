import {
  CheckCircleFilled,
  CloseCircleFilled,
  BuildOutlined,
  LoadingOutlined,
  ClockCircleOutlined,
  DesktopOutlined,
  DownOutlined,
  HddOutlined,
  UnorderedListOutlined
} from '@ant-design/icons'
import { Collapse, Tag, Typography } from 'antd'
import { useId, useMemo, useState } from 'react'
import type { WorkflowBuildExecutionTask } from '../../../../typings'
import { cx } from '../../../../utils'
import {
  buildToolActivityPlacement,
  displayTaskTitle,
  executionTaskGroup,
  executionTaskPlanView,
  taskId,
  taskStatusColor,
  taskStatusText
} from './buildExecutionPresentation'
import { buildTaskDisplayStatus, isCheckpointCandidate } from './buildTaskStatus'
import { taskDependencyNames } from './buildTaskPresentation'
import { BuildExecutionTaskDetails } from './BuildExecutionTaskDetails'
import BuildTaskRetryButton from './BuildTaskRetryButton'
import WorkspaceToolActivity from './WorkspaceToolActivity'
import './BuildExecutionTaskList.less'
import './BuildExecutionTaskAppearance.less'

const { Text } = Typography
const taskGroups = [
  { key: 'frontend', title: '前端任务', icon: <DesktopOutlined /> },
  { key: 'backend', title: '后端任务', icon: <HddOutlined /> },
  { key: 'other', title: '其他任务', icon: <UnorderedListOutlined /> }
]

/** 按正式归属分组展示任务，保留外部传入的组内执行排序及实时状态。 */
export function BuildExecutionTaskList({
  tasks,
  reusableTaskIds = []
}: {
  tasks: WorkflowBuildExecutionTask[]
  reusableTaskIds?: string[]
}): JSX.Element {
  const tasksById = useMemo(
    () => new Map(tasks.map((task) => [taskId(task), executionTaskPlanView(task)])),
    [tasks]
  )
  const reused = new Set(reusableTaskIds)
  return (
    <Collapse
      className={cx('workflow-build-task-groups')}
      bordered={false}
      defaultActiveKey={taskGroups.map((group) => group.key)}
      destroyInactivePanel={false}
      expandIconPosition="right"
    >
      {taskGroups.map((group) => {
        const members = tasks.filter((task) => executionTaskGroup(task) === group.key)
        if (!members.length) return null
        const completed = members.filter(
          (task) => buildTaskDisplayStatus(task.status) === 'completed'
        ).length
        return (
          <Collapse.Panel
            key={group.key}
            header={
              <span className={cx('workflow-build-task-group-heading')}>
                <span className={cx('workflow-build-task-group-icon')} aria-hidden="true">
                  {group.icon}
                </span>
                <Text strong>{group.title}</Text>
                <span className={cx('workflow-build-task-group-count')}>{members.length} 项</span>
                <Text type="secondary" className={cx('workflow-build-task-group-progress')}>
                  {completed}/{members.length} 已完成
                </Text>
              </span>
            }
          >
            <div className={cx('workflow-build-task-list')}>
              {members.map((task) => (
                <BuildExecutionTaskCard
                  key={taskId(task)}
                  task={task}
                  reused={reused.has(taskId(task)) || task.status === 'already_satisfied'}
                  dependencyNames={taskDependencyNames(executionTaskPlanView(task), tasksById)}
                />
              ))}
            </div>
          </Collapse.Panel>
        )
      })}
    </Collapse>
  )
}

/** 状态图标与结果位于标头两端，原有操作留在详情内，收起后保留已访问内容。 */
function BuildExecutionTaskCard({
  task,
  reused,
  dependencyNames
}: {
  task: WorkflowBuildExecutionTask
  reused: boolean
  dependencyNames: string[]
}): JSX.Element {
  const [expanded, setExpanded] = useState(false)
  const [visited, setVisited] = useState(false)
  const contentId = useId()
  const status = buildTaskDisplayStatus(task.status)
  const title = displayTaskTitle(task)
  /** 原生按钮统一处理鼠标与键盘切换，不让文件折叠或重试影响整张卡片。 */
  function toggleDetails(): void {
    setVisited(true)
    setExpanded((current) => !current)
  }
  return (
    <article className={cx('workflow-build-execution-task', status)}>
      <button
        type="button"
        className={cx('workflow-build-execution-task-heading')}
        aria-expanded={expanded}
        aria-controls={contentId}
        aria-label={`${expanded ? '收起' : '展开'}任务详情：${title}，${taskStatusText(status)}`}
        onClick={toggleDetails}
      >
        <span className={cx('workflow-build-execution-task-emblem')} aria-hidden="true">
          <BuildOutlined />
        </span>
        <Text strong>{title}</Text>
        <span className={cx('workflow-build-execution-task-result')}>
          {status === 'completed' ? (
            <CheckCircleFilled aria-hidden="true" />
          ) : status === 'failed' ? (
            <CloseCircleFilled aria-hidden="true" />
          ) : status === 'running' ? (
            <LoadingOutlined aria-hidden="true" />
          ) : (
            <ClockCircleOutlined aria-hidden="true" />
          )}
          {status === 'completed'
            ? '执行成功'
            : status === 'failed'
              ? '执行失败'
              : taskStatusText(status)}
        </span>
        <DownOutlined rotate={expanded ? 180 : 0} aria-hidden="true" />
      </button>
      {!expanded && buildToolActivityPlacement(task, false) === 'header' && (
        <div className={cx('workflow-build-execution-task-activity')}>
          <WorkspaceToolActivity activity={task.activeToolActivity!} />
        </div>
      )}
      <div
        id={contentId}
        hidden={!expanded}
        className={cx('workflow-build-execution-task-content')}
      >
        {visited && (
          <>
            <BuildExecutionTaskDetails
              task={task}
              dependencyNames={dependencyNames}
              expanded={expanded}
            />
            <div className={cx('workflow-build-task-footer')}>
              <BuildTaskRetryButton task={task} />
              {status !== 'completed' && (
                <Tag
                  className={cx('workflow-build-task-status-tag', status)}
                  color={taskStatusColor(status)}
                >
                  {taskStatusText(status)}
                </Tag>
              )}
              {reused && <Tag className={cx('workflow-build-task-checkpoint-tag')}>已复用</Tag>}
              {isCheckpointCandidate(task) && (
                <Tag className={cx('workflow-build-task-checkpoint-tag')}>可形成检查点</Tag>
              )}
            </div>
          </>
        )}
      </div>
    </article>
  )
}
