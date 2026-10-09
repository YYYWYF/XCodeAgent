import MappingDescription from './MappingDescription'
import { DownOutlined, UpOutlined } from '@ant-design/icons'
import { Button, Select, Typography } from 'antd'
import { useEffect, useState } from 'react'
import type { ReactElement } from 'react'
import type { WorkflowApiDatabaseOperation, WorkflowApiDesignDraft, WorkflowApiField } from '../../../../typings'
import type { ApiDesignDatabaseMetadata } from '../../../../service/dataSources'
import type { BindingSelection } from '../../../../typings/endpointDesign'
import { apiDesignFieldKey } from '../WorkflowRunCard/apiDesignSerialization'
import ResponseFieldMapping, { ResponseFieldMappingHeader } from './ResponseFieldMapping'
import { mappingCandidates } from './model'
import { DATABASE_OPERATION_LABELS } from './model'
import DatabaseQueryEditor from './DatabaseQueryEditor'
import DatabaseWriteEditor from './DatabaseWriteEditor'

const CRUD_OPTIONS = Object.entries(DATABASE_OPERATION_LABELS).map(([value, label]) => ({ value, label }))

type Props = {
  fields: WorkflowApiField[]
  selection: BindingSelection & { sourceType: 'database' }
  metadata?: ApiDesignDatabaseMetadata
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
  const [collapsed, setCollapsed] = useState<Record<'query' | 'write' | 'return', boolean>>({ query: !readOnly, write: !readOnly, return: !readOnly })
  // 切换编辑与详情模式时重置默认状态，普通草稿更新保留用户的展开选择。
  useEffect(() => { setCollapsed({ query: !readOnly, write: !readOnly, return: !readOnly }) }, [readOnly])
  const columns = metadata?.columns || []
  const responseFields = fields.filter((field) => field.side === 'response')

  // 仅在收到一次提交校验结果时展开错误区，用户随后仍可手动收起。
  useEffect(() => {
    if (readOnly || !Object.keys(errors).length) return
    setCollapsed((current) => ({
      query: errors.__databaseQuery ? false : current.query,
      write: Object.keys(errors).some((key) => key === '__databaseWrites' || key.startsWith('__databaseWrite:')) ? false : current.write,
      return: fields.some((field) => field.side === 'response' && errors[apiDesignFieldKey(field)]) ? false : current.return
    }))
  }, [errors, readOnly, fields])

  /** 返回字段复用单数据源规则编辑器，不改变来源选择旅程。 */
  const renderField = (field: WorkflowApiField): ReactElement => <ResponseFieldMapping key={apiDesignFieldKey(field)} field={field} fields={fields}
    sources={metadata ? mappingCandidates(field, selection, metadata) : []} draft={draft} readOnly={readOnly} disabled={!editable || busy}
    error={errors[apiDesignFieldKey(field)]} onChange={onChange} />

  /** 展开或收起指定配置区。 */
  const toggle = (section: 'query' | 'write' | 'return'): void => setCollapsed((current) => ({ ...current, [section]: !current[section] }))
  /** 渲染配置区标题及展开按钮。 */
  const heading = (section: 'query' | 'write' | 'return', title: string): ReactElement => <div className="database-section-heading">
    <div className="database-section-title is-card"><h4>{title}</h4></div>
    <Button className="database-section-toggle" type="text" aria-expanded={!collapsed[section]} aria-label={`${collapsed[section] ? '展开' : '收起'}${title}`}
      icon={collapsed[section] ? <DownOutlined /> : <UpOutlined />} onClick={() => toggle(section)} />
  </div>

  return <div className={`database-mapping${readOnly ? ' is-readonly' : ''}`}>
    <section className="binding-section database-operation-section">
      <div className="database-section-heading"><div className="database-section-title is-card"><h4>数据库操作</h4></div>
        {readOnly ? <span className="database-static-operation">{operation ? DATABASE_OPERATION_LABELS[operation] : '—'}</span>
          : <Select disabled={!editable || busy} value={operation} placeholder="选择操作类型" options={CRUD_OPTIONS} onChange={onOperationChange} />}</div>
      {errors.__databaseOperation && !busy ? <small className="database-mapping-error">{errors.__databaseOperation}</small> : null}</section>
    <MappingDescription draft={draft} disabled={!editable || busy} readOnly={readOnly} onChange={onChange} />
    {operation && operation !== 'create' ? <section className={`binding-section database-query-section${collapsed.query ? ' is-collapsed' : ''}`}>{heading('query', '查询条件')}
      {!collapsed.query ? <div className="database-section-content"><DatabaseQueryEditor selection={selection} columns={columns} fields={fields} query={draft.databaseQuery}
        editable={editable} readOnly={readOnly} busy={busy} error={errors.__databaseQuery} onChange={(databaseQuery) => onChange({ ...draft, databaseQuery })} /></div> : null}</section> : null}
    {operation === 'create' || operation === 'update' ? <section className={`binding-section database-write-section${collapsed.write ? ' is-collapsed' : ''}`}>{heading('write', '写入字段')}
      {!collapsed.write ? <div className="database-section-content"><DatabaseWriteEditor selection={selection} columns={columns} fields={fields}
        writes={draft.databaseWrites || []} editable={editable} readOnly={readOnly} busy={busy} errors={errors}
        onChange={(databaseWrites) => onChange({ ...draft, databaseWrites })} /></div> : null}</section> : null}
    <section className={`binding-section database-return-section${collapsed.return ? ' is-collapsed' : ''}`}>{heading('return', '返回字段')}
      {!collapsed.return ? <div className="database-section-content">{responseFields.length
        ? <div className={`database-return-table${readOnly ? ' is-readonly' : ''}`}><ResponseFieldMappingHeader readOnly={readOnly} />{responseFields.map(renderField)}</div>
        : <Typography.Text type="secondary">无返回字段</Typography.Text>}</div> : null}</section>
  </div>
}
