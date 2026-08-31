import { Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import './PageContextHeader.less'

const { Text } = Typography

type PageContextHeaderProps = {
  conversationTitle: string
  historical?: boolean
}

/** 对话顶部只呈现会话身份与历史状态，不承载产物或工作流归属。 */
export default function PageContextHeader({
  conversationTitle,
  historical = false
}: PageContextHeaderProps): ReactElement {
  return (
    <section
      aria-label="当前对话"
      className={cx('page-context-header', 'conversation-context-header')}
    >
      <div className={cx('conversation-context-title')}>
        <MessageTitle title={conversationTitle} />
      </div>
      {historical ? <span className={cx('conversation-history-label')}>历史任务</span> : null}
    </section>
  )
}

/** 让较长的对话标题保持单行截断。 */
function MessageTitle({ title }: { title: string }): ReactElement {
  return (
    <Text strong title={title}>
      {title || '新对话'}
    </Text>
  )
}
