import { Button, Drawer, Space, Table, Tag, Tooltip, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMemo, useState } from 'react'
import type { ReactElement } from 'react'
import type {
  WorkflowApiDesignDraft,
  WorkflowApiDesignPayload,
  WorkflowApiField
} from '../../../../typings'
import {
  projectApiFieldMappingRows,
  type ApiFieldMappingRow
} from './apiDesignTableModel'
import ApiFieldMappingEditor from './ApiFieldMappingEditor'
import { apiFieldLocationLabel } from './apiDesignTableModel'
import { createUnconfiguredFieldMapping, findFieldMapping, replaceFieldMapping } from './apiDesignSerialization'
import './ApiDesignPanel.less'
import type { ApiSourceMetadataAction, ApiSourceMetadataContext, ApiSourceMetadataRequestState } from './apiSourceSelectorModel'

const { Text } = Typography

type Props = {
  activeSide: WorkflowApiField['side']
  disabled?: boolean
  payload: WorkflowApiDesignPayload
  draft: WorkflowApiDesignDraft
  errors: Record<string, string>
  metadataRequest?: ApiSourceMetadataRequestState
  onDraftChange: (draft: WorkflowApiDesignDraft) => void
  onLoadSource: (
    draft: WorkflowApiDesignDraft,
    action: ApiSourceMetadataAction,
    context: ApiSourceMetadataContext
  ) => Promise<void>
}

/** 渲染 Request/Response 字段映射表，并把所有字段映射编辑收敛到抽屉。 */
export default function ApiFieldMappingTable({
  activeSide, disabled, payload, draft, errors, metadataRequest, onDraftChange, onLoadSource
}: Props): ReactElement {
  const [editingFieldId, setEditingFieldId] = useState('')
  const [mappingEndpoint, setMappingEndpoint] = useState<WorkflowApiField | undefined>()

  const rows = useMemo(
    () => projectApiFieldMappingRows(draft, payload, activeSide, errors),
    [activeSide, draft, errors, payload]
  )

  /** 打开字段映射抽屉，不在表格行内写入半成品映射。 */
  const openMappingEditor = (row: ApiFieldMappingRow): void => {
    setEditingFieldId(row.field.id)
    setMappingEndpoint(row.field)
  }

  /** 关闭字段映射抽屉并恢复表格浏览状态。 */
  const closeMappingEditor = (): void => {
    setMappingEndpoint(undefined)
    setEditingFieldId('')
  }

  /** 直接读取结构化映射，分别展示来源、字段和说明，避免拆分拼接后的路径。 */
  const renderMappingCell = (row: ApiFieldMappingRow, column: 'source' | 'field' | 'description'): ReactElement => {
    const mapping = findFieldMapping(draft, row.field)
    let value = ''
    if (mapping?.mappingType === 'source_mapping') {
      value = column === 'description' ? mapping.businessDescription || '' : mapping.sourceFields.map((source) => {
        if (column === 'source') return String(payload.sources?.find((item) => item.id === source.sourceId)?.name || source.sourceId)
        return source.sourceType === 'database'
          ? [source.schema, source.table, source.column].filter(Boolean).join('.')
          : [source.directoryId, source.operationId, source.section, source.path].join(' / ')
      }).join('\n')
    } else if (mapping?.mappingType === 'business_description' && column === 'description') {
      value = mapping.businessDescription
    }
    return value
      ? <div className="api-design-mapping-summary"><Text>{value}</Text></div>
      : <Text type="secondary">—</Text>
  }

  /** 按已保存的映射类型展示模式名称，编辑入口统一放在右侧操作列。 */
  const renderModeCell = (row: ApiFieldMappingRow): ReactElement => {
    const labels = { unconfigured: '未配置', source_mapping: '数据源字段映射', business_description: '业务说明' }
    const mapping = findFieldMapping(draft, row.field)
    const label = mapping?.mappingType === 'source_mapping' ? ({ direct: '直接映射', single_field_description: '单字段业务处理', multi_field_description: '多字段业务处理' })[mapping.processingType] : labels[row.mode]
    return <Text type={row.mode === 'unconfigured' ? 'secondary' : undefined}>{label}</Text>
  }

  const columns: ColumnsType<ApiFieldMappingRow> = [
    {
      title: '字段', dataIndex: 'field', key: 'field', width: 160,
      render: (_value, row) => <div className="api-design-field-cell">
        <Space size={6} wrap>
          <Text strong>{row.field.path}</Text>
        </Space>
        {row.field.description ? <Tooltip title={row.field.description}><Text type="secondary" ellipsis>{row.field.description}</Text></Tooltip> : null}
      </div>
    },
    { title: '位置', key: 'location', width: 125, render: (_value, row) => <Tag className="api-design-location-tag">{apiFieldLocationLabel(row.field.location)}</Tag> },
    { title: '类型', dataIndex: ['field', 'type'], key: 'type', width: 90, render: (_value, row) => <span className="api-design-type-text">{row.field.type}</span> },
    { title: '映射模式', key: 'mode', width: 140, render: (_value, row) => renderModeCell(row) },
    { title: '业务说明', key: 'description', width: 200, render: (_value, row) => renderMappingCell(row, 'description') },
    { title: '数据源', key: 'source', width: 140, render: (_value, row) => renderMappingCell(row, 'source') },
    { title: '映射字段', key: 'sourceField', width: 180, render: (_value, row) => renderMappingCell(row, 'field') },
    {
      title: '操作', key: 'actions', width: 120, align: 'center', fixed: 'right',
      render: (_value, row) => <Button
        aria-label={`${row.mode === 'unconfigured' ? '添加' : '编辑'} ${row.field.path} 映射`}
        className="api-design-row-action"
        disabled={disabled}
        onClick={() => openMappingEditor(row)}
        type="link"
      >{row.mode === 'unconfigured' ? '添加映射' : '编辑映射'}</Button>
    }
  ]

  const tableEmpty = <div className="api-design-table-empty">当前侧没有可映射叶子字段。</div>

  return <>
    <div className="api-design-table-shell">
      <Table<ApiFieldMappingRow>
        className="api-design-table"
        columns={columns}
        dataSource={rows}
        locale={{ emptyText: tableEmpty }}
        pagination={false}
        rowClassName={(row) => [
          'api-design-table-row',
          editingFieldId === row.field.id ? 'is-editing' : '',
          row.status === 'error' ? 'is-error' : ''
        ].filter(Boolean).join(' ')}
        rowKey={(row) => row.key}
        tableLayout="fixed"
        scroll={{ x: 1130 }}
        size="small"
      />
    </div>
    <Drawer
      className="api-design-mapping-drawer"
      destroyOnClose onClose={closeMappingEditor} open={Boolean(mappingEndpoint)}
      title={mappingEndpoint ? `配置 ${mappingEndpoint.path} 的字段映射` : '配置字段映射'} width={680}
    >
      {mappingEndpoint ? <ApiFieldMappingEditor
        endpoint={mappingEndpoint}
        initialMode={(() => {
          const mode = rows.find((row) => row.field.id === mappingEndpoint.id)?.mode
          return mode === 'business_description' ? mode : 'source_mapping'
        })()}
        payload={payload}
        draft={draft}
        disabled={disabled}
        metadataRequest={metadataRequest}
        onCancel={closeMappingEditor}
        onClear={() => {
          onDraftChange(replaceFieldMapping(draft, createUnconfiguredFieldMapping(mappingEndpoint)))
          closeMappingEditor()
        }}
        onLoadSource={onLoadSource}
        onSave={(next) => { onDraftChange(next); closeMappingEditor() }}
      /> : null}
    </Drawer>
  </>
}
