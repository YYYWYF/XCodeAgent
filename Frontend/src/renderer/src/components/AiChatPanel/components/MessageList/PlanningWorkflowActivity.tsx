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
import { planningWorkflowPhase } from '../../../../service/applicationPlanningWorkflowState'
import TechnicalPlanningOutput from './TechnicalPlanningOutput'

const { Text } = Typography

type Props = {
  workflow?: WorkflowRunPayload
  outputEnded?: boolean
}

/** 展示设计变更意图识别和正式产物生成的实时 AG-UI 状态。 */
export default function PlanningWorkflowActivity({ workflow, outputEnded = false }: Props): ReactElement | null {
  const activity = outputEnded && workflow?.technicalPlanningOutput?.entries.length
    ? {
        status: workflow.summary.status === 'failed' ? 'failed' as const : 'completed' as const,
        title: '技术规划输出已结束',
        detail: '本轮实际收到的输出已保留。',
        intentLabel: undefined
      }
    : planningWorkflowActivity(workflow)
  if (!activity) return null
  const technical = outputEnded || planningWorkflowPhase(workflow) === 'technical_planning'

  return (
    <section
      aria-live="polite"
      className={cx('planning-workflow-activity', activity.status, technical && 'has-output')}
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
      {technical && workflow ? <TechnicalPlanningOutput key={workflow.runId} runId={workflow.runId}
        output={workflow.technicalPlanningOutput} running={activity.status === 'running'} /> : null}
    </section>
  )
}
