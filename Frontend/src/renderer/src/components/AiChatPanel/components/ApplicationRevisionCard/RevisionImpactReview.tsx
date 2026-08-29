import { Alert, Button, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { WorkflowRevisionImpact } from '../../../../typings'
import { cx } from '../../../../utils'
import './ApplicationRevisionCard.less'

const { Text } = Typography

type RevisionImpactReviewProps = {
  disabled?: boolean
  impact: WorkflowRevisionImpact
  onDecision: (decision: 'approved' | 'rejected') => void
}

/** 只展示路由模型给出的原因，并保留正式分支进入前的明确确认动作。 */
export default function RevisionImpactReview({
  disabled,
  impact,
  onDecision
}: RevisionImpactReviewProps): ReactElement {
  const designBranch = impact.formalBranch === 'design_stage_revision'
  return (
    <section className={cx('application-revision-impact')}>
      <div className={cx('application-revision-impact-heading')}>
        <Text strong>正式修改确认</Text>
      </div>
      <Alert description={impact.reason} message="修改原因" showIcon type="info" />
      <div className={cx('application-revision-impact-actions')}>
        <Button disabled={disabled} onClick={() => onDecision('rejected')}>
          取消
        </Button>
        <Button disabled={disabled} onClick={() => onDecision('approved')} type="primary">
          {designBranch ? '确认并返回设计阶段' : '确认并进入规划阶段'}
        </Button>
      </div>
    </section>
  )
}
