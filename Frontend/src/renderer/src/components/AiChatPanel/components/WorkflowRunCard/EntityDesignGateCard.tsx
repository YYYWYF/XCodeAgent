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

/** 实体数据源绑定门禁卡片：展示缺失实体并支持一键跳转绑定、完成后再重新检测。 */
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
                  前往绑定
                </Button>
              </div>
            )
          })}
        </div>
      ) : null}
      <div className={cx('workflow-entity-gate-actions')}>
        <Text type="secondary">
          完成实体数据源绑定后点击重新检测，继续当前页面/API开发。
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
