import { Button, Input, Space, Typography } from 'antd'
import type { ReactElement } from 'react'
import { useState } from 'react'
import type { WorkflowRevisionDraftInteraction } from '../../../../typings'
import { cx } from '../../../../utils'
import TechnicalPlanReviewPanel from '../DocPanel/TechnicalPlanReviewPanel'
import './ApplicationRevisionCard.less'

const { Text, Title } = Typography
const { TextArea } = Input

type RevisionDraftReviewProps = {
  disabled?: boolean
  technicalPlan: Record<string, unknown>
  baseline?: Record<string, unknown>
  productPlan: Record<string, unknown>
  interaction: Omit<WorkflowRevisionDraftInteraction, 'action' | 'feedback'>
  onAction: (interaction: WorkflowRevisionDraftInteraction) => void
}

/** 审阅当前唯一正式草稿，并只提交绑定 lifecycle interaction 的结构化动作。 */
export default function RevisionDraftReview({
  disabled,
  technicalPlan,
  baseline,
  productPlan,
  interaction,
  onAction
}: RevisionDraftReviewProps): ReactElement {
  const [feedback, setFeedback] = useState('')

  /** 只有非空反馈才能请求专业 Agent 覆盖生成当前草稿。 */
  const submitRevision = (): void => {
    const normalized = feedback.trim()
    if (!normalized) return
    onAction({ ...interaction, action: 'revise', feedback: normalized })
  }

  return (
    <section className={cx('application-revision-draft')}>
      <div className={cx('application-revision-draft-heading')}>
        <div>
          <Title level={5}>技术规划修订草稿</Title>
          <Text type="secondary">
            请审阅当前草稿。确认后更新正式技术规划，也可以继续提出修改意见。
          </Text>
        </div>
      </div>
      <div className={cx('application-revision-draft-preview')}>
        <TechnicalPlanReviewPanel plan={technicalPlan} productPlan={productPlan} baseline={baseline} />
      </div>
      <div className={cx('application-revision-draft-revise')}>
        <TextArea
          aria-label="草稿修改意见"
          autoSize={{ minRows: 2, maxRows: 5 }}
          disabled={disabled}
          onChange={(event) => setFeedback(event.target.value)}
          placeholder="提出修改，例如：保留现有响应结构，只增加 status 查询参数。"
          value={feedback}
        />
        <Button disabled={disabled || !feedback.trim()} onClick={submitRevision}>
          提出修改
        </Button>
      </div>
      <Space className={cx('application-revision-draft-actions')} wrap>
        <Button
          danger
          disabled={disabled}
          onClick={() => onAction({ ...interaction, action: 'discard' })}
        >
          放弃本次修改
        </Button>
        <span className={cx('application-revision-draft-primary-actions')}>
          <Button
            disabled={disabled}
            onClick={() => onAction({ ...interaction, action: 'confirm' })}
            type="primary"
          >
            确认当前版本
          </Button>
        </span>
      </Space>
    </section>
  )
}
