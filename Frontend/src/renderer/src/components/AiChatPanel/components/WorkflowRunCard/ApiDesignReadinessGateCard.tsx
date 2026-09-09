import { ApiOutlined, CheckOutlined } from '@ant-design/icons'
import { Alert, Button, Tag } from 'antd'
import type { ReactElement } from 'react'
import type { WorkflowApiDesignGateAction } from '../../../../typings'
import { cx } from '../../../../utils'
import type { ApiDesignConfigTarget } from './ApiDesignConfigModal'
import './ApiDesignReadinessGateCard.less'

type DevelopmentTarget = {
  type: 'page' | 'endpoint'
  id: string
  label?: string
  apiContractId?: string
}

type MissingDesignItem = ApiDesignConfigTarget & {
  reason?: string
  status?: string
}

type Props = {
  disabled?: boolean
  message?: string
  missing: Array<Record<string, unknown>>
  onConfigure?: (target: ApiDesignConfigTarget) => void
  onConfirm?: (action: WorkflowApiDesignGateAction) => void
  savedMappingKeys?: ReadonlySet<string>
  scopeKey?: string
  target?: DevelopmentTarget
}

/** 展示开发工作流中的字段映射缺失清单，并提供逐项配置与确认检测入口。 */
export default function ApiDesignReadinessGateCard({
  disabled = false,
  message,
  missing,
  onConfigure,
  onConfirm,
  savedMappingKeys,
  scopeKey = '',
  target
}: Props): ReactElement {
  const items = missing
    .map(normalizeMissingDesign)
    .filter((item): item is MissingDesignItem => Boolean(item))
  const canConfirm = Boolean(target?.id && target?.type)
  return (
    <section className={cx('api-design-readiness-gate')}>
      <Alert
        description="可逐项保存字段映射，全部配置完成后点击确认统一检测。"
        message={message || '存在未完成或已失效的 API 字段映射。'}
        showIcon
        type="warning"
      />
      <div className="api-design-readiness-list">
        {items.map((item) => {
          const mappingKey = `${scopeKey}:${item.apiContractId}:${item.endpointId}`
          const saved = savedMappingKeys?.has(mappingKey) === true
          return (
            <div className="api-design-readiness-item" key={`${item.apiContractId}:${item.endpointId}`}>
              <span className="api-design-readiness-icon" aria-hidden="true"><ApiOutlined /></span>
              <div className="api-design-readiness-copy">
                <strong>{item.label}</strong>
                <span>{saved ? '映射已保存，等待统一检测' : item.reason || '尚未完成字段映射'}</span>
              </div>
              <Tag>{saved ? '已配置，待检测' : item.status === 'stale' ? '需重新配置' : '待配置'}</Tag>
              <Button disabled={disabled} onClick={() => onConfigure?.(item)}>{saved ? '修改映射' : '配置映射'}</Button>
            </div>
          )
        })}
      </div>
      <div className="api-design-readiness-actions">
        <Button
          disabled={disabled || !canConfirm}
          icon={<CheckOutlined />}
          onClick={() => {
            if (!target) return
            onConfirm?.({
              action: 'refresh',
              targetType: target.type,
              targetId: target.id,
              apiContractId: target.type === 'endpoint' ? target.apiContractId : undefined
            })
          }}
          type="primary"
        >
          确认
        </Button>
      </div>
    </section>
  )
}

/** 把后端缺失项规范为独立映射弹窗和门禁列表共用的展示结构。 */
function normalizeMissingDesign(value: Record<string, unknown>):
  | MissingDesignItem
  | undefined {
  const apiContractId = String(value.api_contract_id || value.apiContractId || '')
  const endpointId = String(value.endpoint_id || value.endpointId || '')
  if (!apiContractId || !endpointId) return undefined
  const method = String(value.method || 'API')
  const path = String(value.path || endpointId)
  return {
    apiContractId,
    endpointId,
    label: `${method} ${path}`,
    reason: String(value.reason || ''),
    status: String(value.status || '')
  }
}
