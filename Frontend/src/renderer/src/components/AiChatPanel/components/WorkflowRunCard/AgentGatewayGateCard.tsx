import { ArrowRightOutlined, PauseCircleOutlined } from '@ant-design/icons'
import { Button, Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'

const { Text } = Typography

type Props = {
  agentId: string
  agentLabel: string
  message: string
  onJump: (agentId: string) => void
}

/** 展示 Gateway 对智能体的前置依赖，并进入现有智能体开发任务。 */
export default function AgentGatewayGateCard({
  agentId,
  agentLabel,
  message,
  onJump
}: Props): ReactElement {
  return (
    <div className={cx('workflow-entity-gate-card')}>
      <div className={cx('workflow-entity-gate-head')}>
        <span className={cx('workflow-entity-gate-icon')} aria-hidden="true">
          <PauseCircleOutlined />
        </span>
        <div className={cx('workflow-entity-gate-copy')}>
          <Text strong>智能体接口开发前置</Text>
          <Text type="secondary">{message}</Text>
        </div>
      </div>
      <div className={cx('workflow-entity-gate-item')}>
        <Tag>{agentLabel || agentId || '智能体'}</Tag>
        <Button
          icon={<ArrowRightOutlined />}
          onClick={() => onJump(agentId)}
          size="small"
          type="primary"
        >
          去开发智能体
        </Button>
      </div>
      <Text type="secondary">当前接口开发已停止；点击后进入智能体开发任务。</Text>
    </div>
  )
}
