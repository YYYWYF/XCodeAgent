import { DownOutlined, UpOutlined } from '@ant-design/icons'
import { Button, Select, Tag, Typography } from 'antd'
import { useState } from 'react'
import type { ReactElement } from 'react'
import type { WorkflowApiDatabaseOperation, WorkflowApiDesignDraft, WorkflowApiField, WorkflowApiSourceField } from '../../../../typings'
import type { ApiDesignDatabaseMetadata } from '../../../../service/dataSources'
import type { BindingSelection } from '../../../../typings/endpointDesign'
import { apiDesignFieldKey, endpointFieldSnapshot, replaceFieldMapping } from '../WorkflowRunCard/apiDesignSerialization'
import { DATABASE_OPERATION_LABELS } from './model'
import DatabaseQueryEditor from './DatabaseQueryEditor'
import DatabaseWriteEditor from './DatabaseWriteEditor'

const CRUD_OPTIONS = Object.entries(DATABASE_OPERATION_LABELS).map(([value, label]) => ({ value, label }))

type Props = {
  fields: WorkflowApiField[]
  selection: BindingSelection & { sourceType: 'database' }
  metadata: ApiDesignDatabaseMetadata
  draft: WorkflowApiDesignDraft
  operation?: WorkflowApiDatabaseOperation
  editable: boolean
  readOnly: boolean
  busy: boolean
  errors: Record<string, string>
  onOperationChange: (operation: WorkflowApiDatabaseOperation) => void
  onChange: (draft: WorkflowApiDesignDraft) => void
}

/** 渲染数据库 CRUD 的查询、写入和返回配置。 */
export default function DatabaseMapping({ fields, selection, metadata, draft, operation, editable, readOnly, busy, errors, onOperationChange, onChange }: Props): ReactElement {
  const [collapsed, setCollapsed] = useState<Record<'query' | 'write' | 'return', boolean>>({ query: false, write: false, return: false })
  const columns = metadata.columns || []
  const responseFields = fields.filter((field) => field.side === 'response')

  /** 查找 Endpoint 字段当前选中的数据库来源。 */
  const sourceFor = (field: WorkflowApiField): WorkflowApiSourceField | undefined => {
    const mapping = draft.fieldMappings.find((item) => apiDesignFieldKey(item.endpointField) === apiDesignFieldKey(field))
    return mapping?.mappingType === 'source_mapping' && mapping.sourceFields.length === 1 ? mapping.sourceFields[0] : undefined
  }

  /** 选择或清除写入、返回字段的数据库列。 */
  const setSource = (field: WorkflowApiField, columnName?: string): void => {
    const column = columns.find((item) => item.name === columnName)
    const source: WorkflowApiSourceField | undefined = column ? { sourceType: 'database', sourceId: selection.sourceId, schema: selection.schema,
      table: selection.table, column: column.name, type: column.type, description: column.description, usage: field.side === 'request' ? 'write' : 'read' } : undefined
    const mapping = source ? { endpointField: endpointFieldSnapshot(field), mappingType: 'source_mapping' as const, processingType: 'direct' as const, sourceFields: [source] }
      : { endpointField: endpointFieldSnapshot(field), mappingType: 'unconfigured' as const }
    onChange(replaceFieldMapping(draft, mapping))
  }

  /** 渲染一行可选数据库列的写入或返回字段。 */
  const renderField = (field: WorkflowApiField): ReactElement => {
    const source = sourceFor(field)
    const key = apiDesignFieldKey(field)
    // 未映射状态留到确认时统一校验，避免草稿阶段每行都显示缺失提示。
    const error = errors[key] === 'Endpoint 字段尚未配置映射。' ? undefined : errors[key]
    return <div className={`database-mapping-row${field.side === 'response' ? ' is-read' : ''}${error ? ' is-error' : ''}`} key={key}>
      <div className="database-mapping-field">{field.side === 'response'
        ? <><code className="database-mapping-return-name" title={field.path}>{field.path}</code><span className="database-mapping-return-type">{field.type}</span></>
        : <><Tag>入参</Tag><code>{field.path}</code><span>{field.type}</span></>}</div>
      <span className="database-mapping-arrow" aria-hidden="true">{field.side === 'request' ? '→' : '←'}</span>
      {readOnly ? <span className="database-static-field database-mapping-column">{source?.sourceType === 'database' ? `${source.column} · ${source.type}` : '—'}</span>
        : <Select className="database-mapping-column" allowClear showSearch optionFilterProp="label" disabled={!editable || busy}
          value={source?.sourceType === 'database' ? source.column : undefined} placeholder="选择数据库列"
          options={columns.map((column) => ({ value: column.name, label: `${column.name}${column.description ? `（${column.description}）` : ''} · ${column.type}` }))}
          onChange={(value) => setSource(field, value)} />}
      {error && !busy ? <small className="database-mapping-error">{error}</small> : null}
    </div>
  }

  /** 展开或收起指定配置区。 */
  const toggle = (section: 'query' | 'write' | 'return'): void => setCollapsed((current) => ({ ...current, [section]: !current[section] }))
  /** 渲染配置区标题及展开按钮。 */
  const heading = (section: 'query' | 'write' | 'return', title: string): ReactElement => <div className="database-section-heading">
    <div className="database-section-title is-card"><h4>{title}</h4></div>
    <Button className="database-section-toggle" type="text" aria-expanded={!collapsed[section]} aria-label={`${collapsed[section] ? '展开' : '收起'}${title}`}
      icon={collapsed[section] ? <DownOutlined /> : <UpOutlined />} onClick={() => toggle(section)} />
  </div>

  return <div className={`database-mapping${readOnly ? ' is-readonly' : ''}`}>
    <section className="binding-section database-operation-section"><div className="database-section-heading"><div className="database-section-title is-card"><h4>数据库操作</h4></div>
      {readOnly ? <span className="database-static-operation">{operation ? DATABASE_OPERATION_LABELS[operation] : '—'}</span>
        : <Select disabled={!editable || busy} value={operation} placeholder="选择操作类型" options={CRUD_OPTIONS} onChange={onOperationChange} />}</div>
      {errors.__databaseOperation && !busy ? <small className="database-mapping-error">{errors.__databaseOperation}</small> : null}</section>
    {operation && operation !== 'create' ? <section className={`binding-section database-query-section${collapsed.query ? ' is-collapsed' : ''}`}>{heading('query', '查询条件')}
      {!collapsed.query ? <div className="database-section-content"><DatabaseQueryEditor selection={selection} columns={columns} fields={fields} query={draft.databaseQuery}
        editable={editable} readOnly={readOnly} busy={busy} error={errors.__databaseQuery} onChange={(databaseQuery) => onChange({ ...draft, databaseQuery })} /></div> : null}</section> : null}
    {operation === 'create' || operation === 'update' ? <section className={`binding-section database-write-section${collapsed.write ? ' is-collapsed' : ''}`}>{heading('write', '写入字段')}
      {!collapsed.write ? <div className="database-section-content"><DatabaseWriteEditor selection={selection} columns={columns} fields={fields}
        writes={draft.databaseWrites || []} editable={editable} readOnly={readOnly} busy={busy} errors={errors}
        onChange={(databaseWrites) => onChange({ ...draft, databaseWrites })} /></div> : null}</section> : null}
    <section className={`binding-section database-return-section${collapsed.return ? ' is-collapsed' : ''}`}>{heading('return', '返回字段')}
      {!collapsed.return ? <div className="database-section-content">{responseFields.length
        ? <div className="database-return-table">{responseFields.map(renderField)}</div>
        : <Typography.Text type="secondary">无返回字段</Typography.Text>}</div> : null}</section>
  </div>
}
