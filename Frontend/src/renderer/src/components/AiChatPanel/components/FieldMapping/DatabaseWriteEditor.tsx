import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import { Button, Input, Select, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { WorkflowApiDatabaseWriteDraft, WorkflowApiField } from '../../../../typings'
import type { BindingSelection } from '../../../../typings/endpointDesign'
import { apiDesignFieldKey, endpointFieldSnapshot, typeFamily } from '../WorkflowRunCard/apiDesignSerialization'
import './DatabaseWriteEditor.less'

const WRITE_VALUE_SOURCE_OPTIONS = [
  { value: 'endpoint', label: '接口参数' },
  { value: 'fixed', label: '固定值' },
  { value: 'builtin', label: '内置参数', disabled: true }
]

const WRITE_PARAMETER_LOCATION_LABELS: Partial<Record<WorkflowApiField['location'], string>> = {
  path: 'Path', query: 'Query', header: 'Header', request_body: 'Request Body'
}

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

/** 把写入目标列转换为适合字段类型的固定值。 */
function parseWriteValue(raw: string, columnType: string): unknown {
  if (!raw.trim()) return ''
  const family = typeFamily(columnType)
  if (family === 'number') {
    const value = Number(raw)
    return Number.isFinite(value) ? value : raw
  }
  return raw
}

/** 将已保存的写入固定值转换为表单文本。 */
function writeValueText(value: unknown): string {
  return String(value ?? '')
}

/** 按数据库写入列优先配置目标字段、值来源和接口参数。 */
export default function DatabaseWriteEditor({ selection, columns, fields, writes, editable, readOnly, busy, errors, onChange }: Props): ReactElement {
  const requestFields = fields.filter((field) => field.side === 'request')
  const requestByKey = new Map(requestFields.map((field) => [apiDesignFieldKey(field), field]))

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

  /** 按参数位置分组列出所有接口请求参数。 */
  const parameterGroups = () => ['path', 'query', 'header', 'request_body'].map((location) => ({
    label: WRITE_PARAMETER_LOCATION_LABELS[location as WorkflowApiField['location']],
    options: requestFields.filter((field) => field.location === location).map((field) => ({
      value: apiDesignFieldKey(field), label: `${field.path} · ${field.type}`,
      title: `${field.path}${field.description ? `（${field.description}）` : ''} · ${field.type}`
    }))
  })).filter((group) => group.options.length)

  /** 渲染一行先选目标列、再选值来源及参数的写入配置。 */
  const renderWrite = (write: WorkflowApiDatabaseWriteDraft, index: number): ReactElement => {
    const right = write.right
    const endpointKey = right?.kind === 'endpoint' && right.endpointField
      ? apiDesignFieldKey(right.endpointField)
      : undefined
    const endpointField = endpointKey ? requestByKey.get(endpointKey) : undefined
    // 与查询条件一致：未指定时默认选择接口参数，固定值仍可由用户切换。
    const sourceKind = right?.kind || 'endpoint'
    const fixedValue = right?.kind === 'fixed' ? right.value : undefined
    const targetColumn = columns.find((column) => column.name === write.column)
    const targetLabel = `${write.column || '—'}${targetColumn?.description ? `（${targetColumn.description}）` : ''} · ${write.type}`
    const rowError = errors[`__databaseWrite:${index}`]
    const groups = parameterGroups()

    return <div className={`database-write-row${rowError ? ' is-error' : ''}`} key={`${write.sourceId}:${write.table}:${write.column}:${index}`}>
      <div className="database-write-column-cell">
        {readOnly
          ? <span className="database-write-static-field" title={targetLabel}>{targetLabel}</span>
          : <Select className="database-write-column-select" allowClear showSearch optionFilterProp="label"
            disabled={!editable || busy} placeholder="选择数据表字段" value={write.column || undefined}
            options={columns.map((column) => ({ value: column.name, label: `${column.name}${column.description ? `（${column.description}）` : ''} · ${column.type}` }))}
            onChange={(columnName) => {
              const column = columns.find((item) => item.name === columnName)
              updateWrite(index, { ...write, column: column?.name || '', type: column?.type || 'unknown', description: column?.description || '',
                right: right?.kind === 'fixed' ? { kind: 'fixed', value: '' } : { kind: 'endpoint' } })
            }} />}
      </div>
      <div className="database-write-source-cell">
        {readOnly
          ? <span className="database-write-source-label">{right?.kind === 'endpoint' ? '接口参数' : right?.kind === 'fixed' ? '固定值' : '—'}</span>
          : <Select className="database-write-source-select" disabled={!editable || busy || !write.column}
            placeholder="选择值来源" value={sourceKind} options={WRITE_VALUE_SOURCE_OPTIONS}
            onChange={(kind: 'endpoint' | 'fixed') => updateWrite(index, { ...write,
              right: kind === 'fixed' ? { kind: 'fixed', value: '' } : { kind: 'endpoint' } })} />}
      </div>
      <div className="database-write-parameter-cell">
        {readOnly
          ? right?.kind === 'endpoint'
            ? <span className="database-write-parameter-summary">{endpointField ? <><span className="database-write-parameter-location">{WRITE_PARAMETER_LOCATION_LABELS[endpointField.location] || endpointField.location}</span><span className="database-write-parameter-value">{endpointField.path} · {endpointField.type}</span></> : <span className="database-write-parameter-value">—</span>}</span>
            : <span className="database-write-parameter-value">{right?.kind === 'fixed' ? writeValueText(fixedValue) : '—'}</span>
          : sourceKind === 'endpoint'
            ? <Select className="database-write-parameter-select" showSearch optionFilterProp="title"
              disabled={!editable || busy || !write.column || !groups.length} placeholder="选择接口参数"
              value={endpointField ? endpointKey : undefined} options={groups}
              onChange={(value) => {
                const selectedField = requestByKey.get(value)
                if (selectedField) updateWrite(index, { ...write, right: { kind: 'endpoint', endpointField: endpointFieldSnapshot(selectedField) } })
              }} />
            : sourceKind === 'fixed' && typeFamily(write.type) === 'boolean'
              ? <Select className="database-write-parameter-select" placeholder="选择固定值" disabled={!editable || busy || !write.column}
                value={typeof fixedValue === 'boolean' ? String(fixedValue) : undefined}
                options={[{ value: 'true', label: 'true' }, { value: 'false', label: 'false' }]}
                onChange={(value) => updateWrite(index, { ...write, right: { kind: 'fixed', value: value === 'true' } })} />
              : sourceKind === 'fixed' ? <Input className="database-write-fixed-input" disabled={!editable || busy || !write.column}
                type={typeFamily(write.type) === 'number' ? 'number' : 'text'} placeholder="输入固定值"
                value={writeValueText(fixedValue)}
                onChange={(event) => updateWrite(index, { ...write, right: { kind: 'fixed', value: parseWriteValue(event.target.value, write.type) } })} />
              : null}
      </div>
      {!readOnly ? <Button className="database-write-delete" danger type="text" disabled={!editable || busy}
        icon={<DeleteOutlined />} aria-label="删除写入字段" onClick={() => removeWrite(index)} /> : null}
      {rowError ? <small className="database-write-error">{rowError}</small> : null}
    </div>
  }

  return <div className={`database-write-editor${readOnly ? ' is-readonly' : ''}`}>
    {writes.length ? <div className="database-write-list">
      <div className="database-write-head"><span>数据表字段</span><span>值来源</span><span>参数</span><span aria-hidden="true" /></div>
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
