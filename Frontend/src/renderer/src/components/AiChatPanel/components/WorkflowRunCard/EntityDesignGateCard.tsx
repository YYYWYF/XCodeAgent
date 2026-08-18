import {
  ArrowRightOutlined,
  ExclamationCircleOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
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
  onRetry: () => void
}

/** 实体设计门禁专用卡片：展示缺失实体并支持一键跳转设计、完成后再重新检测。 */
export default function EntityDesignGateCard({
  disabled,
  explanation,
  entities,
  onJump,
  onRetry,
}: EntityDesignGateCardProps): ReactElement {
  return (
    <div className={cx('workflow-entity-gate-card')}>
      <div className={cx('workflow-entity-gate-head')}>
        <span className={cx('workflow-entity-gate-icon')} aria-hidden="true">
          <ExclamationCircleOutlined />
        </span>
        <div className={cx('workflow-entity-gate-copy')}>
          <Text strong>实体设计门禁</Text>
          <Text type="secondary">{explanation}</Text>
        </div>
      </div>
      {entities.length > 0 ? (
        <div className={cx('workflow-entity-gate-list')}>
          <Text type="secondary">请先完成以下实体的设计并确认：</Text>
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
                  前往设计
                </Button>
              </div>
            )
          })}
        </div>
      ) : null}
      <div className={cx('workflow-entity-gate-actions')}>
        <Text type="secondary">
          完成实体设计后点击重新检测，继续生成页面/接口详细设计。
        </Text>
        <Button
          disabled={disabled}
          icon={<ReloadOutlined />}
          onClick={onRetry}
          type="primary"
        >
          重新检测
        </Button>
      </div>
    </div>
  )
}
