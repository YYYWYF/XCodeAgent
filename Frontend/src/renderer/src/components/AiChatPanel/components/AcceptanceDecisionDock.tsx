import { CheckCircleOutlined, CloseCircleOutlined } from '@ant-design/icons'
import { Button, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../utils'
import './AcceptanceDecisionDock.less'

const { Text } = Typography

type Props = {
  disabled?: boolean
  onReject: () => void
  onAccept: () => void
}

/** 验收待确认底栏：拒绝只恢复对话，验收通过暂时只提示未开放。 */
export default function AcceptanceDecisionDock({
  disabled = false,
  onReject,
  onAccept
}: Props): ReactElement {
  return (
    <section aria-label="版本验收" className={cx('acceptance-decision-dock')}>
      <div className={cx('acceptance-decision-copy')}>
        <Text strong>版本验收</Text>
        <Text type="secondary">请根据需求文档基线验收当前应用。</Text>
      </div>
      <div className={cx('acceptance-decision-actions')}>
        <Button
          disabled={disabled}
          icon={<CloseCircleOutlined />}
          onClick={onReject}
          type="default"
        >
          不通过，进入对话
        </Button>
        <Button
          disabled={disabled}
          icon={<CheckCircleOutlined />}
          onClick={onAccept}
          type="primary"
        >
          验收通过
        </Button>
      </div>
    </section>
  )
}
