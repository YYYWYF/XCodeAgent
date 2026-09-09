import { CheckCircleFilled, DownOutlined, UpOutlined } from '@ant-design/icons'
import { Alert, Button, Tag } from 'antd'
import { useState } from 'react'
import type { ReactElement } from 'react'
import type {
  EndpointDesignDetail,
  WorkflowApiDesignGateDesign,
  WorkflowApiDesignGateResult
} from '../../../../typings'
import { cx } from '../../../../utils'
import EndpointDesignResult from '../EndpointDesignResult'
import { endpointDesignSummary } from '../EndpointDesignResult/endpointDesignResultModel'
import './ApiDesignConfirmedCard.less'

type Props = {
  result: WorkflowApiDesignGateResult
  awaitingConfirmation?: boolean
  onConfirm?: () => void
  onEdit?: (item: WorkflowApiDesignGateDesign) => void
  disabled?: boolean
}

/** 将映射结果展示为确认卡，支持开发门禁确认或历史只读查看。 */
export default function ApiDesignConfirmedCard({
  result,
  awaitingConfirmation = false,
  onConfirm,
  onEdit,
  disabled = false
}: Props): ReactElement {
  const [expanded, setExpanded] = useState(true)
  const details = result.designs.map(gateDesignDetail)
  const summary = details.reduce(
    (current, detail) => {
      const item = endpointDesignSummary(detail)
      return {
        requestCount: current.requestCount + item.requestCount,
        responseCount: current.responseCount + item.responseCount
      }
    },
    { requestCount: 0, responseCount: 0 }
  )
  return (
    <section className={cx('api-design-confirmed-card')}>
      <header>
        <span className="confirmation-icon"><CheckCircleFilled /></span>
        <div>
          <div className="confirmation-title"><strong>{awaitingConfirmation ? 'API 映射待确认' : 'API 映射已确认'}</strong><Tag color={awaitingConfirmation ? 'processing' : 'success'}>{awaitingConfirmation ? '待确认' : '成功'}</Tag></div>
          <p>{result.targetLabel} · {result.designs.length} 个 Endpoint · 请求 {summary.requestCount} 项 · 返回 {summary.responseCount} 项</p>
        </div>
      </header>
      <Alert showIcon type="info" message={awaitingConfirmation ? '请确认开发版本' : '确认已提交'} description={awaitingConfirmation ? '以下展示当前 Endpoint 的映射结果，确认后才会进入后续开发。' : '以下展示当次确认的映射结果，后续开发停止或失败不会撤销此结果。'} />
      <Button block aria-expanded={expanded} icon={expanded ? <UpOutlined /> : <DownOutlined />} onClick={() => setExpanded(!expanded)}>
        {expanded ? '收起映射详情' : '展开映射详情'}
      </Button>
      {expanded && (
        <div className="confirmation-design-list">
          {details.map((detail, index) => {
            const item = result.designs[index]
            const endpoint = detail.design?.endpointContract as Record<string, unknown> | undefined
            return (
              <section className="confirmation-design-item" key={`${item.apiContractId}:${item.endpointId}`}>
                <div className="confirmation-design-heading">
                  <strong>{String(endpoint?.method || 'API')} {String(endpoint?.path || item.endpointId)}</strong>
                  {awaitingConfirmation ? <Button disabled={disabled} onClick={() => onEdit?.(item)}>修改映射</Button> : null}
                </div>
                <EndpointDesignResult detail={detail} historyLayout />
              </section>
            )
          })}
        </div>
      )}
      {awaitingConfirmation ? (
        <div className="confirmation-actions">
          <Button disabled={disabled} onClick={onConfirm} type="primary">确认并继续开发</Button>
        </div>
      ) : null}
    </section>
  )
}

/** 把聚合门禁条目转换为复用只读映射视图需要的详情结构。 */
function gateDesignDetail(item: WorkflowApiDesignGateDesign): EndpointDesignDetail {
  return {
    apiContractId: item.apiContractId,
    endpointId: item.endpointId,
    status: 'confirmed',
    designed: true,
    reason: '',
    design: item.design
  }
}
