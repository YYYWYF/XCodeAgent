import { Alert, Collapse, Descriptions, Empty, Table, Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { EndpointDesignDetail } from '../../../../typings'
import { cx } from '../../../../utils'
import { endpointDesignSummary, groupEndpointDesignRows, projectDatabaseWriteRows, projectEndpointDesignRows } from './endpointDesignResultModel'
import './EndpointDesignResult.less'

const { Text } = Typography

type Props = {
  detail?: EndpointDesignDetail
  compact?: boolean
  historyLayout?: boolean
}

const CONDITION_LABELS: Record<string, string> = {
  eq: '等于', ne: '不等于', gt: '大于', gte: '大于等于', lt: '小于', lte: '小于等于',
  contains: '包含', not_contains: '不包含', starts_with: '前缀匹配', ends_with: '后缀匹配',
  in: '属于', not_in: '不属于', between: '介于', not_between: '不介于', is_null: '为空', is_not_null: '不为空'
}

/** 按括号保留查询树的 AND/OR 关系，并展示右值来源。 */
function queryText(value: unknown): string {
  if (!value || typeof value !== 'object') return ''
  const item = value as Record<string, unknown>
  if (item.kind === 'condition') {
    const right = item.right && typeof item.right === 'object' ? item.right as Record<string, unknown> : undefined
    const field = right?.endpointField && typeof right.endpointField === 'object' ? right.endpointField as Record<string, unknown> : undefined
    const rightText = right?.kind === 'endpoint' ? `接口参数 ${String(field?.location || '')}.${String(field?.path || '')}`
      : right?.kind === 'fixed' ? `固定值 ${Array.isArray(right.value) ? right.value.join('，') : String(right.value)}` : ''
    return `${String(item.schema || '')}.${String(item.table || '')}.${String(item.column || '')} ${CONDITION_LABELS[String(item.operator || '')] || String(item.operator || '')} ${rightText}`.trim()
  }
  const parts = Array.isArray(item.items) ? item.items.map(queryText).filter(Boolean) : []
  return parts.length ? `(${parts.join(` ${String(item.join || 'and').toUpperCase()} `)})` : ''
}

/** 渲染 API 设计正式产物的共享只读视图。 */
export default function EndpointDesignResult({ detail, compact = false, historyLayout = false }: Props): ReactElement {
  if (!detail) return <Empty description="尚未读取接口 API 映射结果" />
  const summary = endpointDesignSummary(detail)
  const databaseWriteRows = projectDatabaseWriteRows(detail)
  const statusLabel = detail.status === 'confirmed' ? '已确认' : detail.status === 'stale' ? '已失效' : '待设计'
  const statusColor = detail.status === 'confirmed' ? 'success' : detail.status === 'stale' ? 'warning' : 'default'
  const design = detail.design || {}
  const endpoint = design.endpointContract as Record<string, unknown> | undefined
  const endpointName = String(endpoint?.name || '').trim()
  const columns = [
    { title: '字段', dataIndex: 'endpoint', key: 'endpoint', width: 160 },
    { title: '位置', dataIndex: 'locationLabel', key: 'location', width: 125 },
    { title: '类型', dataIndex: 'type', key: 'type', width: 90 },
    { title: '映射模式', dataIndex: 'mapping', key: 'mapping', width: 140 },
    { title: '业务说明', dataIndex: 'description', key: 'description', width: 200, render: (value: string) => value || '—' },
    { title: '数据源', dataIndex: 'dataSource', key: 'dataSource', width: 140, render: (value: string) => value || '—' },
    { title: '映射字段', dataIndex: 'mappingField', key: 'mappingField', width: 180, render: (value: string) => value || '—' },
  ]
  const databaseWriteColumns = [
    { title: '数据表字段', dataIndex: 'target', key: 'target', width: 240, render: (value: string) => value || '—' },
    { title: '值来源', dataIndex: 'valueSource', key: 'valueSource', width: 110 },
    { title: '参数或固定值', dataIndex: 'value', key: 'value', width: 240, render: (value: string) => value || '—' },
    { title: '类型', dataIndex: 'type', key: 'type', width: 100 }
  ]
  const renderRows = (side: 'request' | 'response'): ReactElement => {
    const groups = groupEndpointDesignRows(projectEndpointDesignRows(detail, side))
    if (!groups.length) return <Empty description="暂无映射字段" image={Empty.PRESENTED_IMAGE_SIMPLE} />
    return (
      <Collapse className="endpoint-design-result-groups" defaultActiveKey={groups.map((group) => group.location)}>
        {groups.map((group) => (
          <Collapse.Panel header={`${group.label}（${group.rows.length}）`} key={group.location}>
            <Table columns={columns} dataSource={group.rows} pagination={false} size="small" scroll={{ x: 1130 }} />
          </Collapse.Panel>
        ))}
      </Collapse>
    )
  }

  if (historyLayout) {
    return (
      <div className={cx('endpoint-design-result', 'endpoint-design-history')}>
        <section><strong>数据库操作</strong><p>{String(design.databaseOperation || '无数据库映射')}</p></section>
        {design.databaseQuery ? <section><strong>查询条件</strong><p>{queryText(design.databaseQuery)}</p></section> : null}
        {databaseWriteRows.length ? <section><strong>数据库写入字段</strong><Table columns={databaseWriteColumns} dataSource={databaseWriteRows} pagination={false} size="small" /></section> : null}
        {design.implementationDescription ? <section><strong>API 实现描述</strong><p>{String(design.implementationDescription)}</p></section> : null}
        {(['request', 'response'] as const).map((side) => (
          <section key={side}>
            <header><strong>{side === 'request' ? '请求映射' : '返回映射'}</strong><Tag>{side === 'request' ? summary.requestCount : summary.responseCount} 项</Tag></header>
            <Table columns={columns} dataSource={projectEndpointDesignRows(detail, side)} pagination={false} size="small" scroll={{ x: 1130 }} />
          </section>
        ))}
      </div>
    )
  }

  return (
    <div className={cx('endpoint-design-result')}>
      <div className="endpoint-design-result-heading">
        <div>
          <Typography.Title level={5}>接口 API 映射结果</Typography.Title>
          {endpointName ? <Text strong>{endpointName}</Text> : null}
          <Text code>{String(endpoint?.method || 'API')} {String(endpoint?.path || detail.endpointId)}</Text>
        </div>
        <Tag color={statusColor}>{statusLabel}</Tag>
      </div>
      {detail.reason ? <Alert message={detail.reason} showIcon type={detail.status === 'stale' ? 'warning' : 'info'} /> : null}
      <Descriptions bordered column={1} size="small">
        <Descriptions.Item label="API Contract">{detail.apiContractId}</Descriptions.Item>
        <Descriptions.Item label="接口">{endpointName || detail.endpointId}</Descriptions.Item>
        <Descriptions.Item label="接口 ID">{detail.endpointId}</Descriptions.Item>
        <Descriptions.Item label="实现描述">{String(design.implementationDescription || '未填写')}</Descriptions.Item>
        <Descriptions.Item label="数据库操作">{String(design.databaseOperation || '无数据库映射')}</Descriptions.Item>
        <Descriptions.Item label="映射数量">请求 {summary.requestCount} 项，返回 {summary.responseCount} 项</Descriptions.Item>
      </Descriptions>
      {design.databaseQuery ? <section className="endpoint-design-fixed-conditions"><Text strong>查询条件</Text><div>{queryText(design.databaseQuery)}</div></section> : null}
      {databaseWriteRows.length ? <section className="endpoint-design-fixed-conditions"><Text strong>数据库写入字段</Text><Table columns={databaseWriteColumns} dataSource={databaseWriteRows} pagination={false} size="small" /></section> : null}
      {!compact ? (
        <Collapse defaultActiveKey={['request', 'response']}>
          {(['request', 'response'] as const).map((side) => (
            <Collapse.Panel header={`${side === 'request' ? '请求映射' : '返回映射'}（${projectEndpointDesignRows(detail, side).length}）`} key={side}>
              {renderRows(side)}
            </Collapse.Panel>
          ))}
        </Collapse>
      ) : (
        <Text type="secondary">展开查看完整请求与返回字段映射。</Text>
      )}
    </div>
  )
}
