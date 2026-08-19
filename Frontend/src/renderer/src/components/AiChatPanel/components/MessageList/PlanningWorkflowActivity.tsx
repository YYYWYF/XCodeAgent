import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined
} from '@ant-design/icons'
import { Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { WorkflowRunPayload } from '../../../../typings'
import { cx } from '../../../../utils'
import { planningWorkflowActivity } from '../../../Welcome/planningWorkflowState'

const { Text } = Typography

type Props = {
  workflow?: WorkflowRunPayload
}

/** 展示设计变更意图识别和正式产物生成的实时 AG-UI 状态。 */
export default function PlanningWorkflowActivity({ workflow }: Props): ReactElement | null {
  const activity = planningWorkflowActivity(workflow)
  if (!activity) return null

  return (
    <section
      aria-live="polite"
      className={cx('planning-workflow-activity', activity.status)}
    >
      <span className={cx('planning-workflow-activity-icon')} aria-hidden="true">
        {activity.status === 'running' ? (
          <LoadingOutlined spin />
        ) : activity.status === 'failed' ? (
          <CloseCircleOutlined />
        ) : (
          <CheckCircleOutlined />
        )}
      </span>
      <div className={cx('planning-workflow-activity-copy')}>
        {activity.intentLabel ? (
          <Tag className={cx('planning-workflow-activity-intent')}>
            意图识别：{activity.intentLabel}
          </Tag>
        ) : null}
        <Text strong>{activity.title}</Text>
        <Text type="secondary">{activity.detail}</Text>
      </div>
    </section>
  )
}
