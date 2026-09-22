import { ApiOutlined, CheckOutlined } from '@ant-design/icons'
import { Alert, Button, Tag } from 'antd'
import type { ReactElement } from 'react'
import type { WorkflowApiDesignGateAction, WorkflowApiDesignReadinessItem } from '../../../../typings'
import { cx } from '../../../../utils'
import type { ApiDesignConfigTarget } from './ApiDesignConfigModal'
import './ApiDesignReadinessGateCard.less'

type DevelopmentTarget = {
  type: 'page' | 'endpoint'
  id: string
  label?: string
  apiContractId?: string
}

type ApiDesignItem = ApiDesignConfigTarget & {
  designed?: boolean
  method?: string
  path?: string
  reason?: string
  status?: string
}

type Props = {
  disabled?: boolean
  message?: string
  designs: WorkflowApiDesignReadinessItem[]
  onConfigure?: (target: ApiDesignConfigTarget) => void
  onConfirm?: (action: WorkflowApiDesignGateAction) => void
  savedMappingKeys?: ReadonlySet<string>
  scopeKey?: string
  target?: DevelopmentTarget
}

/** 展示开发工作流中的 API 设计状态清单，并提供逐项查看或配置与确认检测入口。 */
export default function ApiDesignReadinessGateCard({
  disabled = false,
  message,
  designs,
  onConfigure,
  onConfirm,
  savedMappingKeys,
  scopeKey = '',
  target
}: Props): ReactElement {
  const items = designs
    .map(normalizeApiDesign)
    .filter((item): item is ApiDesignItem => Boolean(item))
  const allReady = items.length > 0 && items.every((item) => item.designed)
  const canConfirm = Boolean(target?.id && target?.type)
  return (
    <section className={cx('api-design-readiness-gate', allReady && 'api-design-readiness-gate-complete')}>
      <Alert
        description={
          allReady
            ? '可逐项查看或修改字段映射，确认并检测后继续开发。'
            : '可逐项保存字段映射，全部配置完成后点击确认统一检测。'
        }
        message={
          allReady
            ? message || '当前目标的字段映射已准备。'
            : message || '存在未完成或已失效的 API 字段映射。'
        }
        showIcon
        type={allReady ? 'success' : 'warning'}
      />
      <div className="api-design-readiness-section-heading">
        <h3>相关接口</h3>
        <span>{items.length}个接口</span>
      </div>
      <div className="api-design-readiness-list">
        {items.map((item) => {
          const mappingKey = `${scopeKey}:${item.apiContractId}:${item.endpointId}`
          const saved = savedMappingKeys?.has(mappingKey) === true
          return (
            <div
              className={cx(
                'api-design-readiness-item',
                item.designed && 'api-design-readiness-item-completed'
              )}
              key={`${item.apiContractId}:${item.endpointId}`}
            >
              <span className="api-design-readiness-icon" aria-hidden="true"><ApiOutlined /></span>
              <div className="api-design-readiness-copy">
                <div className="api-design-readiness-endpoint">
                  <Tag className="api-design-readiness-method">{item.method}</Tag>
                  <strong>{item.path}</strong>
                </div>
                <span>
                  {saved
                    ? '映射已保存，等待统一检测'
                    : item.designed
                      ? '字段映射已完成，可在右侧查看'
                      : item.reason || '尚未完成字段映射'}
                </span>
              </div>
              <Tag
                className={cx(
                  'api-design-readiness-status',
                  saved
                    ? 'api-design-readiness-status-saved'
                    : item.designed
                      ? 'api-design-readiness-status-completed'
                      : item.status === 'stale'
                        ? 'api-design-readiness-status-stale'
                        : 'api-design-readiness-status-pending'
                )}
              >
                {saved ? '已配置，待检测' : item.designed ? '已完成' : item.status === 'stale' ? '需重新配置' : '待配置'}
              </Tag>
              <Button className="api-design-readiness-configure-button" disabled={disabled} onClick={() => onConfigure?.(item)}>
                {saved || item.designed ? '查看映射' : '配置映射'}
              </Button>
            </div>
          )
        })}
      </div>
      <div className="api-design-readiness-actions">
        <Button
          className="api-design-readiness-confirm-button"
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
          确认并检测
        </Button>
      </div>
    </section>
  )
}

/** 把后端全量 API 设计状态规范为字段映射工作台和门禁列表共用的展示结构。 */
function normalizeApiDesign(value: WorkflowApiDesignReadinessItem):
  | ApiDesignItem
  | undefined {
  const apiContractId = String(value.api_contract_id || '')
  const endpointId = String(value.endpoint_id || '')
  if (!apiContractId || !endpointId) return undefined
  const method = String(value.method || 'API')
  const path = String(value.path || endpointId)
  return {
    apiContractId,
    endpointId,
    label: `${method} ${path}`,
    method,
    path,
    designed: value.designed === true || String(value.status || '') === 'confirmed',
    reason: String(value.reason || ''),
    status: String(value.status || '')
  }
}
