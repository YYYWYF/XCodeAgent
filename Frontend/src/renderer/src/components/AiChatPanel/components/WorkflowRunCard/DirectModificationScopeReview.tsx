import { Typography } from 'antd'
import type { ReactElement } from 'react'
import type { WorkflowClarification } from '../../../../typings'
import { cx } from '../../../../utils'
import './DirectModificationScopeReview.less'

const { Text } = Typography

/** 展示模型逐条整理的需求理解，供用户确认或补充。 */
export default function DirectModificationScopeReview({
  clarification
}: {
  clarification: WorkflowClarification
}): ReactElement {
  const clarifiedRequest = String(clarification.clarifiedRequest || '').trim()
  const clarifiedRequestItems = clarifiedRequest
    .split(/\r?\n/)
    .map((item) => item.replace(/^\s*(?:\d+[.)、]|[-*•])\s*/, '').trim())
    .filter(Boolean)

  return (
    <section className={cx('direct-modification-scope-review')}>
      <div className={cx('direct-modification-scope-section')}>
        <Text className={cx('direct-modification-scope-label')} strong>
          AI 对需求的理解
        </Text>
        <div className={cx('direct-modification-scope-request')}>
          <ol className={cx('direct-modification-scope-request-list')}>
            {(clarifiedRequestItems.length > 0 ? clarifiedRequestItems : ['需求内容尚未整理。'])
              .map((item, index) => (
                <li key={`${index}-${item}`}>{item}</li>
              ))}
          </ol>
        </div>
      </div>
      <Text className={cx('direct-modification-scope-follow-up')} type="secondary">
        如需补充或修正，可在下方输入后发送，模型会重新整理需求。
      </Text>
    </section>
  )
}
