import { ApiOutlined, BookOutlined, CopyOutlined, DeleteOutlined, DownOutlined, EditOutlined, FileTextOutlined, SendOutlined, UpOutlined } from '@ant-design/icons'
import { Button, Tag, Tooltip, Typography, message } from 'antd'
import type { ReactElement, ReactNode } from 'react'
import { useState } from 'react'
import type { DataSourceDirectory, DataSourceOperation } from '../../typings'
import { cx } from '../../utils'
import { JsonSampleTabs } from './JsonStructureViewer'

const { Text, Title } = Typography

type DetailParameter = { name: string; type: string; required: boolean; description: string }
type DetailCardProps = { children: ReactNode; collapsible?: boolean; icon: ReactNode; title: string; tone: 'basic' | 'request' | 'response' }

/** 渲染详情卡片，并为请求参数、请求体和响应体提供独立折叠状态。 */
function DetailCard({ children, collapsible = true, icon, title, tone }: DetailCardProps): ReactElement {
  const [expanded, setExpanded] = useState(true)
  const heading = <span className={cx('data-source-operation-detail-card-heading')}><span className={cx('data-source-operation-detail-card-icon', `tone-${tone}`)}>{icon}</span><strong>{title}</strong></span>
  return (
    <section className={cx('data-source-operation-detail-card', `tone-${tone}`, !expanded && 'is-collapsed')}>
      {collapsible ? <button aria-expanded={expanded} className={cx('data-source-operation-detail-card-header')} onClick={() => setExpanded((current) => !current)} type="button">{heading}<span className={cx('data-source-operation-detail-card-arrow')}>{expanded ? <UpOutlined /> : <DownOutlined />}</span></button> : <div className={cx('data-source-operation-detail-card-header')}>{heading}</div>}
      {expanded ? <div className={cx('data-source-operation-detail-card-body')}>{children}</div> : null}
    </section>
  )
}

/** 渲染请求参数或 Header 的统一四列表格。 */
function ParameterTable({ items }: { items: DetailParameter[] }): ReactElement {
  return <div className={cx('data-source-operation-detail-parameter-table')}><table><thead><tr><th>参数名</th><th>类型</th><th>是否必填</th><th>描述</th></tr></thead><tbody>{items.map((item, index) => <tr key={`${item.name}-${index}`}><td>{item.name || '—'}</td><td><code>{item.type}</code></td><td><span className={cx('data-source-operation-detail-required', item.required ? 'is-required' : 'is-optional')}>{item.required ? '是' : '否'}</span></td><td>{item.description || '—'}</td></tr>)}</tbody></table></div>
}

/** 渲染请求参数卡片中的 Path、Query 或 Header 分组。 */
function ParameterGroup({ items, title }: { items: DetailParameter[]; title: string }): ReactElement {
  const [expanded, setExpanded] = useState(false)
  return <section className={cx('data-source-operation-detail-parameter-group', !expanded && 'is-collapsed')}>
    <button aria-expanded={expanded} className={cx('data-source-operation-detail-parameter-toggle')} onClick={() => setExpanded((current) => !current)} type="button">
      <span className={cx('data-source-operation-detail-parameter-title')}><strong>{title}</strong><span>{items.length}</span></span>
      <span className={cx('data-source-operation-detail-parameter-arrow')}>{expanded ? <UpOutlined /> : <DownOutlined />}</span>
    </button>
    {expanded ? <div className={cx('data-source-operation-detail-parameter-content')}><ParameterTable items={items} /></div> : null}
  </section>
}

/** 判断接口是否配置了可展示的 JSON 内容。 */
function hasJsonContent(value: unknown, structure: DataSourceOperation['requestStructure']): boolean {
  return structure !== null || (value !== undefined && value !== null)
}

/** 将路径或查询参数转换为详情表格行。 */
function toDetailParameters(parameters: DataSourceOperation['pathParameters']): DetailParameter[] {
  return parameters.map((parameter) => ({ name: parameter.name, type: parameter.type, required: parameter.required, description: parameter.description }))
}

/** 将 Header 转换为统一的详情表格行，Header 值作为描述信息展示。 */
function toHeaderParameters(operation: DataSourceOperation): DetailParameter[] {
  return operation.headers.map((header) => ({ name: header.name, type: 'string', required: false, description: header.value }))
}

/** 渲染选中接口的只读配置详情，空的可选模块不占用页面空间。 */
export default function DataSourceOperationDetails({ directory, operation, onDelete, onEdit, showHeader = true }: { directory: DataSourceDirectory; operation: DataSourceOperation; onDelete: () => void; onEdit: () => void; showHeader?: boolean }): ReactElement {
  const pathParameters = toDetailParameters(operation.pathParameters)
  const queryParameters = toDetailParameters(operation.queryParameters)
  const headerParameters = toHeaderParameters(operation)
  const hasRequestParameters = pathParameters.length > 0 || queryParameters.length > 0 || headerParameters.length > 0
  const hasRequestBody = hasJsonContent(operation.requestSample, operation.requestStructure)
  const hasResponseBody = hasJsonContent(operation.responseSample, operation.responseStructure)

  /** 将请求路径写入系统剪贴板，并反馈复制结果。 */
  const copyOperationPath = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(operation.path)
      message.success('请求路径已复制')
    } catch {
      message.error('复制失败，请手动复制请求路径')
    }
  }

  return (
    <article className={cx('data-source-manager-detail', !showHeader && 'data-source-manager-detail-workbench')}>
      {showHeader ? <header className={cx('data-source-manager-detail-header')}>
        <div className={cx('data-source-manager-detail-title')}><div><Title level={4}>{operation.name}</Title><Text type="secondary">{directory.name}</Text></div><Tag>{operation.method}</Tag></div>
        <div className={cx('data-source-manager-detail-actions')}><Button icon={<EditOutlined />} onClick={onEdit}>编辑接口</Button><Button danger icon={<DeleteOutlined />} onClick={onDelete} type="text">删除</Button></div>
      </header> : null}
      <div className={cx('data-source-manager-detail-body')}>
        <DetailCard collapsible={false} icon={<BookOutlined />} title="基本信息" tone="basic">
          <div className={cx('data-source-operation-detail-basic-info')}>
            <div><span>接口名称</span><strong>{operation.name}</strong></div>
            <div><span>请求方法</span><Tag className={cx('data-source-operation-detail-method')}>{operation.method}</Tag></div>
            <div><span>请求路径</span><span className={cx('data-source-operation-detail-path')}><code>{operation.path}</code><Tooltip title="复制路径"><Button aria-label="复制请求路径" icon={<CopyOutlined />} onClick={() => void copyOperationPath()} size="small" type="text" /></Tooltip></span></div>
            <div><span>接口描述</span><Text type={operation.description ? undefined : 'secondary'}>{operation.description || '—'}</Text></div>
          </div>
        </DetailCard>
        {hasRequestParameters ? <DetailCard icon={<SendOutlined />} title="请求参数" tone="request">
          <div className={cx('data-source-operation-detail-parameter-groups')}>
            {pathParameters.length ? <ParameterGroup items={pathParameters} title="Path 参数" /> : null}
            {queryParameters.length ? <ParameterGroup items={queryParameters} title="Query 参数" /> : null}
            {headerParameters.length ? <ParameterGroup items={headerParameters} title="Header 参数" /> : null}
          </div>
        </DetailCard> : null}
        {hasRequestBody ? <DetailCard icon={<ApiOutlined />} title="请求体" tone="request"><JsonSampleTabs showTitle={false} structure={operation.requestStructure} label="请求体" value={operation.requestSample} /></DetailCard> : null}
        {hasResponseBody ? <DetailCard icon={<FileTextOutlined />} title="响应体" tone="response"><JsonSampleTabs showTitle={false} structure={operation.responseStructure} label="响应体" value={operation.responseSample} /></DetailCard> : null}
      </div>
    </article>
  )
}
