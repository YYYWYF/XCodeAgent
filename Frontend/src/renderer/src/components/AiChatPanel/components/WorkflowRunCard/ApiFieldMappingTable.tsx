import { Button, Drawer, Space, Table, Tag, Tooltip, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMemo, useState } from 'react'
import type { ReactElement } from 'react'
import type {
  WorkflowApiDesignDraft,
  WorkflowApiDesignPayload,
  WorkflowApiField
} from '../../../../typings'
import { cx } from '../../../../utils'
import {
  projectApiFieldMappingRows,
  pruneUnusedSceneEntities,
  type ApiFieldMappingRow
} from './apiDesignTableModel'
import ApiFieldMappingEditor from './ApiFieldMappingEditor'
import { apiFieldLocationLabel } from './apiDesignTableModel'
import { createUnconfiguredFieldMapping, replaceFieldMapping } from './apiDesignSerialization'
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

  /** 打开直接映射或经实体映射抽屉，不在表格行内写入半成品映射。 */
  const openMappingEditor = (row: ApiFieldMappingRow): void => {
    setEditingFieldId(row.field.id)
    setMappingEndpoint(row.field)
  }

  /** 关闭字段映射抽屉并恢复表格浏览状态。 */
  const closeMappingEditor = (): void => {
    setMappingEndpoint(undefined)
    setEditingFieldId('')
  }

  /** 为实体选择器构造当前模板字段的只读选项。 */
  const renderEntityCell = (row: ApiFieldMappingRow): ReactElement => {
    if (row.mode === 'direct_source' || row.mode === 'business_description') return <Text type="secondary">—</Text>
    return <SummaryTags values={row.entityLabels} />
  }

  /** 为数据源单元格渲染摘要或当前行的级联选择器。 */
  const renderSourceCell = (row: ApiFieldMappingRow): ReactElement => {
    if (row.mode === 'business_description') return <Text type="secondary">—</Text>
    return <SummaryTags values={row.sourceLabels} />
  }

  const columns: ColumnsType<ApiFieldMappingRow> = [
    {
      title: '字段', dataIndex: 'field', key: 'field', width: 170,
      render: (_value, row) => <div className="api-design-field-cell">
        <Space size={6} wrap>
          <Text strong>{row.field.path}</Text>
          <Tag>{row.field.required ? '必填' : '可选'}</Tag>
        </Space>
        {row.field.description ? <Tooltip title={row.field.description}><Text type="secondary" ellipsis>{row.field.description}</Text></Tooltip> : null}
      </div>
    },
    { title: '位置', key: 'location', width: 110, render: (_value, row) => apiFieldLocationLabel(row.field.location) },
    { title: '类型', dataIndex: ['field', 'type'], key: 'type', width: 110 },
    {
      title: '映射模式', key: 'mode', width: 150,
      render: (_value, row) => row.mode === 'unconfigured'
          ? <Button disabled={disabled} onClick={() => openMappingEditor(row)} size="small" type="link">配置映射</Button>
          : <Space size={4}><Tag>{row.mode === 'through_entity' ? '经实体' : row.mode === 'business_description' ? '业务说明' : '直接'}</Tag><Button disabled={disabled} onClick={() => openMappingEditor(row)} size="small" type="link">编辑映射</Button></Space>
    },
    { title: '实体映射', key: 'entity', width: 230, render: (_value, row) => renderEntityCell(row) },
    { title: '数据源映射', key: 'source', width: 360, render: (_value, row) => renderSourceCell(row) },
    {
      title: '状态', key: 'status', width: 120,
      render: (_value, row) => <Tooltip title={row.errorMessages.join('；') || undefined}><Tag className="api-design-status" color={statusColor(row.status)}>{statusLabel(row.status)}</Tag></Tooltip>
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
        rowClassName={(row) => cx('api-design-table-row', editingFieldId === row.field.id && 'is-editing', row.status === 'error' && 'is-error')}
        rowKey={(row) => row.key}
        scroll={{ x: 1250 }}
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
          return mode === 'through_entity' || mode === 'business_description' ? mode : 'direct_source'
        })()}
        payload={payload}
        draft={draft}
        disabled={disabled}
        metadataRequest={metadataRequest}
        onCancel={closeMappingEditor}
        onClear={() => {
          onDraftChange(pruneUnusedSceneEntities(
            replaceFieldMapping(draft, createUnconfiguredFieldMapping(mappingEndpoint))
          ))
          closeMappingEditor()
        }}
        onLoadSource={onLoadSource}
        onSave={(next) => { onDraftChange(next); closeMappingEditor() }}
      /> : null}
    </Drawer>
  </>
}

/** 显示实体或来源摘要，避免把多字段规则挤成一行长文本。 */
function SummaryTags({ values }: { values: string[] }): ReactElement {
  if (!values.length) return <Text type="secondary">—</Text>
  return <Space className="api-design-mapping-summary" size={[4, 4]} wrap>{values.map((value) => <Tag key={value}>{value}</Tag>)}</Space>
}

/** 将内部状态转换为用户可读状态文案。 */
function statusLabel(status: ApiFieldMappingRow['status']): string {
  return ({ completed: '已完成', required_missing: '待配置', optional_unmapped: '可选未配置', error: '有错误' } as Record<ApiFieldMappingRow['status'], string>)[status]
}

/** 为字段状态选择 Ant Design 语义颜色。 */
function statusColor(status: ApiFieldMappingRow['status']): string | undefined {
  if (status === 'completed') return 'success'
  if (status === 'required_missing') return 'error'
  if (status === 'error') return 'error'
  return undefined
}
