import { Alert, Collapse, Descriptions, Empty, Table, Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { EndpointDesignDetail } from '../../../../typings'
import { cx } from '../../../../utils'
import { endpointDesignSummary, groupEndpointDesignRows, projectEndpointDesignRows } from './endpointDesignResultModel'
import './EndpointDesignResult.less'

const { Text } = Typography

type Props = {
  detail?: EndpointDesignDetail
  compact?: boolean
  historyLayout?: boolean
}

/** 渲染 API 设计正式产物的共享只读视图。 */
export default function EndpointDesignResult({ detail, compact = false, historyLayout = false }: Props): ReactElement {
  if (!detail) return <Empty description="尚未读取 Endpoint API 映射结果" />
  const summary = endpointDesignSummary(detail)
  const statusLabel = detail.status === 'confirmed' ? '已确认' : detail.status === 'stale' ? '已失效' : '待设计'
  const statusColor = detail.status === 'confirmed' ? 'success' : detail.status === 'stale' ? 'warning' : 'default'
  const design = detail.design || {}
  const endpoint = design.endpointContract as Record<string, unknown> | undefined
  const columns = [
    { title: '字段', dataIndex: 'endpoint', key: 'endpoint', width: 160 },
    { title: '位置', dataIndex: 'locationLabel', key: 'location', width: 125 },
    { title: '类型', dataIndex: 'type', key: 'type', width: 90 },
    { title: '映射模式', dataIndex: 'mapping', key: 'mapping', width: 140 },
    { title: '业务说明', dataIndex: 'description', key: 'description', width: 200, render: (value: string) => value || '—' },
    { title: '数据源', dataIndex: 'dataSource', key: 'dataSource', width: 140, render: (value: string) => value || '—' },
    { title: '映射字段', dataIndex: 'mappingField', key: 'mappingField', width: 180, render: (value: string) => value || '—' }
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
          <Typography.Title level={5}>Endpoint API 映射结果</Typography.Title>
          <Text code>{String(endpoint?.method || 'API')} {String(endpoint?.path || detail.endpointId)}</Text>
        </div>
        <Tag color={statusColor}>{statusLabel}</Tag>
      </div>
      {detail.reason ? <Alert message={detail.reason} showIcon type={detail.status === 'stale' ? 'warning' : 'info'} /> : null}
      <Descriptions bordered column={1} size="small">
        <Descriptions.Item label="API Contract">{detail.apiContractId}</Descriptions.Item>
        <Descriptions.Item label="Endpoint">{detail.endpointId}</Descriptions.Item>
        <Descriptions.Item label="实现描述">{String(design.implementationDescription || '未填写')}</Descriptions.Item>
        <Descriptions.Item label="映射数量">请求 {summary.requestCount} 项，返回 {summary.responseCount} 项</Descriptions.Item>
      </Descriptions>
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
