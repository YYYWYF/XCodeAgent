import { Alert, Button, Input, Space, Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import { useState } from 'react'
import type {
  WorkflowRevisionDraft,
  WorkflowRevisionDraftInteraction
} from '../../../../typings'
import { cx } from '../../../../utils'
import './ApplicationRevisionCard.less'

const { Text, Title } = Typography
const { TextArea } = Input

type RevisionDraftReviewProps = {
  disabled?: boolean
  draft: WorkflowRevisionDraft
  interaction: Omit<WorkflowRevisionDraftInteraction, 'action' | 'editedMarkdown' | 'feedback'>
  onAction: (interaction: WorkflowRevisionDraftInteraction) => void
}

/** 审阅当前唯一正式草稿，并只提交绑定 lifecycle interaction 的结构化动作。 */
export default function RevisionDraftReview({
  disabled,
  draft,
  interaction,
  onAction
}: RevisionDraftReviewProps): ReactElement {
  const [markdown, setMarkdown] = useState(() => draft.markdown)
  const [feedback, setFeedback] = useState('')
  const dirty = markdown !== draft.markdown

  /** 提交保存或确认时始终携带编辑器中的最新 Markdown。 */
  const submitMarkdownAction = (action: 'save' | 'confirm'): void => {
    onAction({ ...interaction, action, editedMarkdown: markdown })
  }

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
          <Title level={5}>
            {draft.artifactKey === 'technical-plan' ? 'TechnicalPlan 重新规划结果' : '正式产物草稿'}
          </Title>
          <Text type="secondary">
            {draft.artifactKey === 'technical-plan'
              ? '已回到技术规划节点重新生成；确认后立即覆盖当前 canonical，并继续收口受影响下游。'
              : '确认后立即覆盖当前 canonical，并继续收口受影响下游。'}
          </Text>
        </div>
        <Tag>{draft.artifactKey}</Tag>
      </div>
      <Alert
        description="保存只更新当前草稿，不会确认产物。放弃只删除当前未确认草稿，已经确认的计划不会恢复或回滚。"
        message="确认边界"
        showIcon
        type="info"
      />
      <TextArea
        aria-label="正式产物 Markdown 草稿"
        autoSize={{ minRows: 12, maxRows: 24 }}
        disabled={disabled}
        onChange={(event) => setMarkdown(event.target.value)}
        spellCheck={false}
        value={markdown}
      />
      <div className={cx('application-revision-draft-status')}>
        <Text type={dirty ? 'warning' : 'secondary'}>
          {dirty ? '有尚未保存的 Markdown 修改' : '草稿已与服务端版本同步'}
        </Text>
        <Text type="secondary">直接上游：{draft.basedOn.map((item) => item.artifactKey).join('、') || '无'}</Text>
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
          <Button disabled={disabled || !dirty} onClick={() => submitMarkdownAction('save')}>
            保存草稿
          </Button>
          <Button disabled={disabled} onClick={() => submitMarkdownAction('confirm')} type="primary">
            确认当前版本
          </Button>
        </span>
      </Space>
    </section>
  )
}
