import { DownOutlined, UpOutlined } from '@ant-design/icons'
import { Typography } from 'antd'
import { useState } from 'react'
import type { ReactElement, ReactNode } from 'react'
import { cx } from '../../../../utils'
import './ToolActivityChain.less'

const { Text } = Typography

type Props = {
  count: number
  isLast: boolean
  latestDetail?: string
  latestIcon: ReactNode
  latestTitle: string
  children: ReactNode
}

/** 将多条工具步骤收拢为一条最新活动，并允许用户展开完整调用链。 */
export default function ToolActivityChain({
  children,
  count,
  isLast,
  latestDetail,
  latestIcon,
  latestTitle
}: Props): ReactElement {
  const [expanded, setExpanded] = useState(false)
  const detail = latestDetail?.trim()
  const summary = detail ? `已记录 ${count} 次调用 · ${detail}` : `已记录 ${count} 次调用`

  return (
    <section aria-label="工具调用链" className={cx('tool-activity-chain', isLast && 'last')}>
      <div className={cx('tool-activity-chain-summary')}>
        <span className={cx('tool-activity-chain-icon')}>{latestIcon}</span>
        <span className={cx('tool-activity-chain-copy')}>
          <Text strong>{latestTitle}</Text>
          <Text type="secondary" title={summary}>
            {summary}
          </Text>
        </span>
        <button
          aria-expanded={expanded}
          className={cx('tool-activity-chain-toggle')}
          onClick={() => setExpanded((current) => !current)}
          type="button"
        >
          {expanded ? <UpOutlined /> : <DownOutlined />}
          <span>{expanded ? '收起调用链' : '展开调用链'}</span>
        </button>
      </div>
      {expanded && <div className={cx('tool-activity-chain-body')}>{children}</div>}
    </section>
  )
}
