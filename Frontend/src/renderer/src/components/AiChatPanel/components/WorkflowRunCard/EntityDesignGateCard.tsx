import { ArrowRightOutlined, ExclamationCircleOutlined } from '@ant-design/icons'
import { Button, Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'

const { Text } = Typography

type EntityDesignGateEntity = {
  entity_id?: string
  entity_name?: string
}

type EntityDesignGateCardProps = {
  disabled?: boolean
  explanation: string
  entities: EntityDesignGateEntity[]
  onJump: (entityId: string) => void
}

/** 实体数据源绑定门禁卡片：展示尚缺实体并在当前会话补齐前置条件。 */
export default function EntityDesignGateCard({
  disabled,
  explanation,
  entities,
  onJump
}: EntityDesignGateCardProps): ReactElement {
  return (
    <div className={cx('workflow-entity-gate-card')}>
      <div className={cx('workflow-entity-gate-head')}>
        <span className={cx('workflow-entity-gate-icon')} aria-hidden="true">
          <ExclamationCircleOutlined />
        </span>
        <div className={cx('workflow-entity-gate-copy')}>
          <Text strong>实体数据源绑定前置</Text>
          <Text type="secondary">{explanation}</Text>
        </div>
      </div>
      {entities.length > 0 ? (
        <div className={cx('workflow-entity-gate-list')}>
          <Text type="secondary">请先完成以下实体的数据源绑定并确认：</Text>
          {entities.map((entity) => {
            const entityId = String(entity.entity_id || '').trim()
            if (!entityId) return null
            const entityName = String(entity.entity_name || entityId).trim()
            return (
              <div className={cx('workflow-entity-gate-item')} key={entityId}>
                <Tag>{entityName}</Tag>
                <Button
                  disabled={disabled}
                  icon={<ArrowRightOutlined />}
                  onClick={() => onJump(entityId)}
                  size="small"
                  type="primary"
                >
                  去设计实体
                </Button>
              </div>
            )
          })}
        </div>
      ) : null}
      <div className={cx('workflow-entity-gate-actions')}>
        <Text type="secondary">全部所需实体确认后，会显示原任务的继续开发按钮。</Text>
      </div>
    </div>
  )
}
