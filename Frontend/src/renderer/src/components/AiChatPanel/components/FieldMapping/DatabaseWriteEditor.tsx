import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import { Button, Select, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { WorkflowApiDatabaseWriteDraft, WorkflowApiField } from '../../../../typings'
import type { BindingSelection } from '../../../../typings/endpointDesign'
import ValueRuleEditor from './ValueRuleEditor'
import './DatabaseWriteEditor.less'

type Props = {
  selection: BindingSelection & { sourceType: 'database' }
  columns: Array<{ name: string; type: string; description?: string }>
  fields: WorkflowApiField[]
  writes: WorkflowApiDatabaseWriteDraft[]
  editable: boolean
  readOnly: boolean
  busy: boolean
  errors: Record<string, string>
  onChange: (writes: WorkflowApiDatabaseWriteDraft[]) => void
}

/** 按数据库写入列优先配置目标字段、值来源和接口参数。 */
export default function DatabaseWriteEditor({ selection, columns, fields, writes, editable, readOnly, busy, errors, onChange }: Props): ReactElement {
  /** 追加绑定当前表的写入行，并将值来源默认为接口参数。 */
  const addWrite = (): void => onChange([...writes, {
    sourceType: 'database', sourceId: selection.sourceId, schema: selection.schema, table: selection.table,
    column: '', type: 'unknown', right: { kind: 'endpoint' }
  }])

  /** 更新一行写入配置，并保留其他目标列。 */
  const updateWrite = (index: number, value: WorkflowApiDatabaseWriteDraft): void => {
    onChange(writes.map((write, rowIndex) => rowIndex === index ? value : write))
  }

  /** 删除指定写入行。 */
  const removeWrite = (index: number): void => onChange(writes.filter((_, rowIndex) => rowIndex !== index))

  /** 渲染一行先选目标列、再选值来源及参数的写入配置。 */
  const renderWrite = (write: WorkflowApiDatabaseWriteDraft, index: number): ReactElement => {
    const right = write.right
    const targetColumn = columns.find((column) => column.name === write.column)
    const targetLabel = `${write.column || '—'}${targetColumn?.description ? `（${targetColumn.description}）` : ''} · ${write.type}`
    const rowError = errors[`__databaseWrite:${index}`]

    return <div className={`database-write-row${rowError ? ' is-error' : ''}`} key={`${write.sourceId}:${write.table}:${write.column}:${index}`}>
      <div className="database-write-column-cell">
        {readOnly
          ? <span className="database-write-static-field" title={targetLabel}><code>{write.column}</code>{targetColumn?.description ? <span className="field-detail-meaning">（{targetColumn.description}）</span> : null}<span className="field-detail-type">{write.type}</span></span>
          : <Select className="database-write-column-select" allowClear showSearch optionFilterProp="label"
            disabled={!editable || busy} placeholder="选择数据表字段" value={write.column || undefined}
            options={columns.map((column) => ({ value: column.name, label: `${column.name}${column.description ? `（${column.description}）` : ''} · ${column.type}` }))}
            onChange={(columnName) => {
              const column = columns.find((item) => item.name === columnName)
              updateWrite(index, { ...write, column: column?.name || '', type: column?.type || 'unknown', description: column?.description || '',
                right: right?.kind === 'fixed' ? { kind: 'fixed', value: '' } : { kind: 'endpoint' } })
            }} />}
      </div>
      <ValueRuleEditor title={write.column || '写入字段'} right={right} fields={fields} type={write.type} readOnly={readOnly}
        disabled={!editable || busy || !write.column} onChange={(right) => updateWrite(index, { ...write, right })} />
      {!readOnly ? <Button className="database-write-delete" danger type="text" disabled={!editable || busy}
        icon={<DeleteOutlined />} aria-label="删除写入字段" onClick={() => removeWrite(index)} /> : null}
      {rowError ? <small className="database-write-error">{rowError}</small> : null}
    </div>
  }

  return <div className={`database-write-editor${readOnly ? ' is-readonly' : ''}`}>
    {writes.length ? <div className="database-write-list">
      <div className="database-write-head"><span>数据表字段</span><span>值来源</span><span>参数</span>{!readOnly ? <span aria-hidden="true" /> : null}</div>
      {writes.map(renderWrite)}
    </div> : <div className="database-write-empty">
      <Typography.Text type="secondary">尚未配置写入字段</Typography.Text>
    </div>}
    {!readOnly ? <div className="database-write-actions">
      <Button icon={<PlusOutlined />} disabled={!editable || busy || !columns.length} onClick={addWrite}>添加写入字段</Button>
    </div> : null}
    {errors.__databaseWrites && !busy ? <small className="database-write-error">{errors.__databaseWrites}</small> : null}
  </div>
}
