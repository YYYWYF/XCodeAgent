import {
  CodeOutlined,
  FileTextOutlined,
  PlayCircleOutlined,
  ReloadOutlined
} from '@ant-design/icons'
import { Alert, Button, Collapse, Space, Typography } from 'antd'
import type {
  WorkflowBuildTargetReview,
  WorkflowBuildTaskPlan,
  WorkflowBuildTaskPlanConfirmation
} from '../../../../typings'
import { cx } from '../../../../utils'
import {
  ReusedCapabilitySummary,
  TargetScopeReview,
  TaskDetails,
  TaskHeader
} from './BuildTaskPlanConfirmationContent'

import './BuildTaskPlanConfirmation.less'
import './BuildTaskPlanConfirmationTasks.less'

type BuildTaskPlanConfirmationProps = {
  disabled?: boolean
  dockedActions?: boolean
  plan?: WorkflowBuildTaskPlan
  targetReview?: WorkflowBuildTargetReview
  errors?: string[]
  onSubmit: (action: WorkflowBuildTaskPlanConfirmation) => void
}

/** 展示当前开发目标和范围内任务的只读确认信息。 */
export default function BuildTaskPlanConfirmation({
  disabled,
  dockedActions,
  plan,
  targetReview,
  errors,
  onSubmit
}: BuildTaskPlanConfirmationProps): JSX.Element {
  const tasks = Array.isArray(plan?.scopeTasks) ? plan.scopeTasks : []
  const reusedPrerequisites = Array.isArray(plan?.reusedPrerequisites)
    ? plan.reusedPrerequisites
    : []
  const retainedSummary = plan?.retainedTaskSummary

  return (
    <div className={cx('workflow-dag-confirmation', dockedActions && 'docked-actions')}>
      <div className={cx('workflow-dag-confirmation-scroll-region')}>
        <header className={cx('workflow-dag-confirmation-overview')}>
          <span className={cx('workflow-dag-confirmation-overview-icon')} aria-hidden="true">
            <CodeOutlined />
          </span>
          <span className={cx('workflow-dag-confirmation-overview-text')}>
            <Typography.Text strong>任务计划待确认</Typography.Text>
            <Typography.Text type="secondary">
              请核对本次开发目标、修改范围和验收标准，确认后进入 Build
            </Typography.Text>
          </span>
          <span className={cx('workflow-dag-confirmation-count')}>{tasks.length} 个任务</span>
        </header>

        {errors && errors.length > 0 ? (
          <Alert
            className={cx('workflow-dag-confirmation-errors')}
            description={errors.join('；')}
            message="任务计划暂不能确认"
            showIcon
            type="warning"
          />
        ) : null}

        {targetReview ? <TargetScopeReview review={targetReview} /> : null}

        <ReusedCapabilitySummary
          prerequisites={reusedPrerequisites}
          retainedSummary={retainedSummary}
        />

        <section className={cx('workflow-dag-confirmation-task-section')}>
          <div className={cx('workflow-dag-confirmation-section-heading')}>
            <span>
              <FileTextOutlined aria-hidden="true" />
              <Typography.Text strong>本次开发任务</Typography.Text>
            </span>
            <Typography.Text type="secondary">默认收起，按需查看任务详情</Typography.Text>
          </div>
          {tasks.length > 0 ? (
            <Collapse
              className={cx('workflow-dag-confirmation-tasks')}
              expandIconPosition="right"
            >
              {tasks.map((task, index) => (
                <Collapse.Panel
                  header={<TaskHeader index={index} task={task} />}
                  key={task.id}
                >
                  <TaskDetails task={task} />
                </Collapse.Panel>
              ))}
            </Collapse>
          ) : (
            <div className={cx('workflow-dag-confirmation-empty')}>暂无本次范围内的任务</div>
          )}
        </section>
      </div>

      <footer className={cx('workflow-dag-confirmation-actions')}>
        <Space size={8} wrap>
          <Button
            disabled={disabled}
            icon={<ReloadOutlined />}
            onClick={() => onSubmit({ mode: 'build_task_plan_confirmation', action: 'regenerate' })}
            size="small"
          >
            重新生成
          </Button>
          <Button
            disabled={disabled || tasks.length === 0}
            icon={<PlayCircleOutlined />}
            onClick={() => onSubmit({ mode: 'build_task_plan_confirmation', action: 'confirm' })}
            size="small"
            type="primary"
          >
            确认并进入 Build
          </Button>
        </Space>
      </footer>
    </div>
  )
}
