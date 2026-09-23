import { DeleteOutlined, DownOutlined, PlusOutlined, UpOutlined } from '@ant-design/icons'
import { Button, Input, Select, Tag, Typography } from 'antd'
import { useState } from 'react'
import type { ReactElement } from 'react'
import type {
  WorkflowApiDatabaseConditionOperator,
  WorkflowApiDatabaseOperation,
  WorkflowApiDesignDraft,
  WorkflowApiField,
  WorkflowApiFilterOperator,
  WorkflowApiSourceField
} from '../../../../typings'
import type { ApiDesignDatabaseMetadata } from '../../../../service/dataSources'
import type { BindingSelection } from '../../../../typings/endpointDesign'
import { allowedDatabaseConditionOperators, allowedFilterOperators, apiDesignFieldKey, defaultDatabaseUsageForOperation, endpointFieldSnapshot, typeFamily } from '../WorkflowRunCard/apiDesignSerialization'
import { DATABASE_OPERATION_LABELS, createDatabaseCondition, operationUsages } from './model'

const OPERATOR_LABELS: Record<WorkflowApiFilterOperator, string> = {
  eq: '等于', ne: '不等于', gt: '大于', gte: '大于等于', lt: '小于', lte: '小于等于',
  contains: '包含', not_contains: '不包含', starts_with: '前缀匹配', ends_with: '后缀匹配',
  in: '属于', not_in: '不属于', between: '介于', not_between: '不介于'
}

const CONDITION_OPERATOR_LABELS: Record<WorkflowApiDatabaseConditionOperator, string> = {
  ...OPERATOR_LABELS,
  is_null: '为空',
  is_not_null: '不为空'
}

const CRUD_OPTIONS = Object.entries(DATABASE_OPERATION_LABELS).map(([value, label]) => ({ value, label }))

/** 将固定条件输入转换为契约值，集合和区间使用逗号分隔。 */
function parseConditionValue(raw: string, operator: WorkflowApiDatabaseConditionOperator, columnType: string): unknown {
  if (operator === 'is_null' || operator === 'is_not_null') return undefined
  const family = typeFamily(columnType)
  const parseScalar = (value: string): unknown => {
    const trimmed = value.trim()
    if (family === 'number') return Number(trimmed)
    if (family === 'boolean') return trimmed === 'true'
    return trimmed
  }
  if (['in', 'not_in', 'between', 'not_between'].includes(operator)) return raw.split(',').map(parseScalar).filter((value) => value !== '')
  return parseScalar(raw)
}

/** 判断固定条件草稿是否已具备可提交的值形态。 */
function conditionValueReady(raw: string, operator: WorkflowApiDatabaseConditionOperator, columnType: string): boolean {
  if (operator === 'is_null' || operator === 'is_not_null') return true
  const values = raw.split(',').map((value) => value.trim()).filter(Boolean)
  const family = typeFamily(columnType)
  if (family === 'number' && values.some((value) => !Number.isFinite(Number(value)))) return false
  if (family === 'boolean' && values.some((value) => value !== 'true' && value !== 'false')) return false
  if (operator === 'between' || operator === 'not_between') {
    if (values.length !== 2) return false
    return family === 'number' ? Number(values[0]) <= Number(values[1]) : values[0] <= values[1]
  }
  if (operator === 'in' || operator === 'not_in') return values.length > 0
  return raw.trim().length > 0
}

/** 将已保存的固定值还原为输入框可编辑的文本。 */
function conditionValueInput(value: unknown): string {
  return Array.isArray(value) ? value.join(',') : String(value ?? '')
}

/** 以只读字段摘要展示数据库列，保留列名、说明和类型。 */
function renderColumnSummary(column: { name: string; type: string; description?: string }, className = ''): ReactElement {
  const label = `${column.name}${column.description ? `（${column.description}）` : ''}`
  return <div className={`database-static-field ${className}`} title={`${label} · ${column.type}`}>
    <span className="database-static-field-name">{label}</span><code className="database-static-field-type">{column.type}</code>
  </div>
}

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

type SectionTitleProps = {
  title: string
  level?: 'card' | 'group'
}

/** 统一渲染数据库映射卡片及内部区域的标题层级。 */
function SectionTitle({ title, level = 'card' }: SectionTitleProps): ReactElement {
  const Heading = level === 'card' ? 'h4' : 'h5'
  return <div className={`database-section-title is-${level}`}><Heading>{title}</Heading></div>
}

/** 渲染数据库 CRUD 的查询条件、写入字段、返回字段和固定条件。 */
export default function DatabaseMapping({ fields, selection, metadata, draft, operation, editable, readOnly, busy, errors, onOperationChange, onChange }: Props): ReactElement {
  const [conditionValueInputs, setConditionValueInputs] = useState<Record<string, string>>({})
  const [pendingFilterOperators, setPendingFilterOperators] = useState<Record<string, WorkflowApiFilterOperator>>({})
  const [collapsedSections, setCollapsedSections] = useState<Record<'query' | 'write' | 'return', boolean>>({ query: false, write: false, return: false })
  const columns = metadata.columns || []
  const requestFields = fields.filter((field) => field.side === 'request')
  const responseFields = fields.filter((field) => field.side === 'response')
  /** 根据 Endpoint 字段键读取当前直接映射。 */
  const mappingFor = (field: WorkflowApiField) => draft.fieldMappings.find((mapping) => apiDesignFieldKey(mapping.endpointField) === apiDesignFieldKey(field))
  /** 读取字段对应的单一数据库来源列。 */
  const sourceFor = (field: WorkflowApiField): WorkflowApiSourceField | undefined => {
    const mapping = mappingFor(field)
    return mapping?.mappingType === 'source_mapping' && mapping.sourceFields.length === 1 ? mapping.sourceFields[0] : undefined
  }
  /** 读取字段当前用途；未配置字段按 CRUD 默认规则进入待配置卡片。 */
  const usageFor = (field: WorkflowApiField): 'filter' | 'write' | 'read' => {
    const source = sourceFor(field)
    if (source?.sourceType === 'database' && source.usage) return source.usage
    return field.side === 'response' ? 'read' : defaultDatabaseUsageForOperation(field, operation)
  }
  /** 写入或清除一条数据库列映射，并在查询用途下设置默认 eq。 */
  const setSource = (field: WorkflowApiField, columnName?: string, usage?: 'filter' | 'write' | 'read', filterOperator?: WorkflowApiFilterOperator): void => {
    if (!columnName) {
      onChange({ ...draft, fieldMappings: draft.fieldMappings.map((mapping) => apiDesignFieldKey(mapping.endpointField) === apiDesignFieldKey(field) ? { endpointField: endpointFieldSnapshot(field), mappingType: 'unconfigured' } : mapping) })
      return
    }
    const column = columns.find((item) => item.name === columnName)
    if (!column) return
    const resolvedUsage = usage || (field.side === 'response' ? 'read' : 'filter')
    const source: WorkflowApiSourceField = {
      sourceType: 'database', sourceId: selection.sourceId, schema: selection.schema, table: selection.table,
      column: column.name, type: column.type, description: column.description, usage: resolvedUsage,
      filterOperator: resolvedUsage === 'filter' ? (filterOperator || 'eq') : undefined
    }
    const next = { endpointField: endpointFieldSnapshot(field), mappingType: 'source_mapping' as const, processingType: 'direct' as const, sourceFields: [source] }
    onChange({ ...draft, fieldMappings: draft.fieldMappings.map((mapping) => apiDesignFieldKey(mapping.endpointField) === apiDesignFieldKey(field) ? next : mapping) })
  }
  /** 在 update 操作中切换请求字段的查询/写入用途。 */
  const updateUsage = (field: WorkflowApiField, usage: 'filter' | 'write'): void => {
    const source = sourceFor(field)
    if (!source || source.sourceType !== 'database') return
    setSource(field, source.column, usage, usage === 'filter' ? (source.filterOperator || 'eq') : undefined)
  }
  /** 渲染一条请求或响应字段行，统一列选择、运算符和错误状态。 */
  const renderField = (field: WorkflowApiField, usage: 'filter' | 'write' | 'read'): ReactElement => {
    const source = sourceFor(field)
    const fieldKey = apiDesignFieldKey(field)
    const sourceInvalid = source?.sourceType === 'database' && (
      source.sourceId !== selection.sourceId || source.schema !== selection.schema || source.table !== selection.table
      || !columns.some((column) => column.name === source.column)
    )
    const validationError = errors[fieldKey]
    // 初次配置时用空选择框表达待完成状态，不把正常流程中的未映射字段渲染成红色错误。
    const mappingError = validationError === 'Endpoint 字段尚未配置映射。'
      ? undefined
      : validationError || (sourceInvalid ? '数据库字段已失效，请重新选择当前数据表中的字段。' : undefined)
    const options = columns.map((column) => ({ value: column.name, label: `${column.name}${column.description ? `（${column.description}）` : ''} · ${column.type}` }))
    const operatorOptions = source?.sourceType === 'database'
      ? allowedFilterOperators(field.type, source.type).map((value) => ({ value, label: OPERATOR_LABELS[value] }))
      : allowedFilterOperators(field.type).map((value) => ({ value, label: OPERATOR_LABELS[value] }))
    const filterOperator = source?.sourceType === 'database' && source.filterOperator
      ? source.filterOperator
      : pendingFilterOperators[fieldKey] || 'eq'
    const usages = operationUsages(field, operation)
    const canChangeUsage = operation === 'update' && field.side === 'request'
    const sourceColumn = source?.sourceType === 'database' ? { name: source.column, type: source.type, description: source.description } : undefined
    return <div className={`database-mapping-row${usage === 'filter' ? ' is-filter' : ''}${usage === 'read' ? ' is-read' : ''}${canChangeUsage ? ' has-usage' : ''}${mappingError ? ' is-error' : ''}`} key={fieldKey}>
      <div className="database-mapping-field">{usage === 'read'
        ? <><code className="database-mapping-return-name" title={field.path}>{field.path}</code><span className="database-mapping-return-type">{field.type}</span></>
        : usage === 'filter'
          ? <><span className="database-mapping-filter-name" title={field.path}><code>{field.path}</code>{field.description ? <span>（{field.description}）</span> : null}</span><span className="database-mapping-filter-type">{field.type}</span></>
          : <><Tag>入参</Tag><span className="database-mapping-endpoint-chip"><code>{field.path}</code>{field.description ? <span>（{field.description}）</span> : null}</span></>}</div>
      <span className="database-mapping-arrow" aria-hidden="true">{usage === 'read' ? '←' : '→'}</span>
      {usage === 'filter' ? readOnly ? <span className="database-static-operator database-mapping-operator">{source?.sourceType === 'database' && source.filterOperator ? OPERATOR_LABELS[source.filterOperator] : '—'}</span> : <Select className="database-mapping-operator" disabled={!editable || busy} options={operatorOptions} value={filterOperator} onChange={(value) => {
        setPendingFilterOperators((current) => ({ ...current, [fieldKey]: value }))
        if (source?.sourceType === 'database' && source.column) setSource(field, source.column, 'filter', value)
      }} /> : null}
      {readOnly ? sourceColumn ? renderColumnSummary(sourceColumn, 'database-mapping-column') : <span className="database-static-empty database-mapping-column">—</span> : <Select className="database-mapping-column" allowClear showSearch optionFilterProp="label" disabled={!editable || busy} options={options} value={source?.sourceType === 'database' ? source.column : undefined} placeholder="选择数据库列" onChange={(value) => setSource(field, value, usage === 'read' ? 'read' : usage, usage === 'filter' ? filterOperator : undefined)} />}
      {canChangeUsage ? readOnly ? <span className="database-static-operator database-mapping-usage">{usage === 'filter' ? '查询条件' : '写入字段'}</span> : <Select className="database-mapping-usage" disabled={!editable || busy} options={usages} value={source?.sourceType === 'database' && source.usage === 'write' ? 'write' : usage === 'write' ? 'write' : 'filter'} onChange={(value) => { if (value === 'filter' || value === 'write') updateUsage(field, value) }} /> : null}
      {mappingError && !busy ? <small className="database-mapping-error">{mappingError}</small> : null}
    </div>
  }
  const queryFields = requestFields.filter((field) => usageFor(field) === 'filter')
  const writeFields = requestFields.filter((field) => usageFor(field) === 'write')
  /** 根据列类型和运算符渲染固定条件行的固定值控件。 */
  const renderConditionValueEditor = (columnType: string, operator: WorkflowApiDatabaseConditionOperator, rawValue: string, onValueChange: (value: string) => void, disabled: boolean, onBlur?: () => void): ReactElement => {
    if (operator === 'is_null' || operator === 'is_not_null') return <span className="database-condition-value-placeholder">该条件无需固定值</span>
    if (typeFamily(columnType) === 'boolean') {
      return <Select disabled={disabled} value={rawValue || undefined} placeholder="选择固定值" options={[{ value: 'true', label: 'true' }, { value: 'false', label: 'false' }]} onChange={onValueChange} />
    }
    const isCollection = ['in', 'not_in', 'between', 'not_between'].includes(operator)
    const isScalarNumber = typeFamily(columnType) === 'number' && !isCollection
    return <Input className={isScalarNumber ? 'database-condition-number-input' : undefined} disabled={disabled} value={rawValue} type={isScalarNumber ? 'number' : 'text'} placeholder={['in', 'not_in'].includes(operator) ? '输入固定值，多个值用逗号分隔' : ['between', 'not_between'].includes(operator) ? '输入下界,上界' : '输入固定值'} onChange={(event) => onValueChange(event.target.value)} onBlur={onBlur} />
  }
  /** 直接更新指定固定条件行，输入不完整时保留空值供现有校验提示。 */
  const updateCondition = (index: number, columnName: string, operator: WorkflowApiDatabaseConditionOperator, rawValue: string): void => {
    const column = columns.find((item) => item.name === columnName)
    const value = column && conditionValueReady(rawValue, operator, column.type) ? parseConditionValue(rawValue, operator, column.type) : undefined
    const next = createDatabaseCondition(selection, column || { name: '', type: '' }, operator, value)
    onChange({ ...draft, databaseConditions: (draft.databaseConditions || []).map((item, rowIndex) => rowIndex === index ? next : item) })
  }
  /** 点击新增时立即插入可编辑的固定条件行并展开查询区域。 */
  const addConditionRow = (): void => {
    setCollapsedSections((current) => ({ ...current, query: false }))
    onChange({ ...draft, databaseConditions: [...(draft.databaseConditions || []), createDatabaseCondition(selection, { name: '', type: '' }, 'eq')] })
  }
  /** 删除指定行，并清理暂存的固定值输入。 */
  const removeCondition = (index: number): void => {
    onChange({ ...draft, databaseConditions: (draft.databaseConditions || []).filter((_, rowIndex) => rowIndex !== index) })
    setConditionValueInputs({})
  }
  /** 切换映射卡片的展开状态，数据库操作卡片始终保持可见。 */
  const toggleSection = (section: 'query' | 'write' | 'return'): void => setCollapsedSections((current) => ({ ...current, [section]: !current[section] }))
  /** 渲染卡片右侧的展开控制，并提供明确的无障碍状态。 */
  const renderSectionToggle = (section: 'query' | 'write' | 'return', label: string): ReactElement => <Button className="database-section-toggle" type="text" aria-expanded={!collapsedSections[section]} aria-label={`${collapsedSections[section] ? '展开' : '收起'}${label}`} icon={collapsedSections[section] ? <DownOutlined /> : <UpOutlined />} onClick={() => toggleSection(section)} />
  return <div className={`database-mapping${readOnly ? ' is-readonly' : ''}`}>
    <section className="binding-section database-operation-section"><div className="database-section-heading"><SectionTitle title="数据库操作" />{readOnly ? <span className="database-static-operation">{operation ? DATABASE_OPERATION_LABELS[operation] : '—'}</span> : <Select disabled={!editable || busy} value={operation} placeholder="选择操作类型" options={CRUD_OPTIONS} onChange={onOperationChange} />}</div>{errors.__databaseOperation && !busy ? <small className="database-mapping-error database-operation-error">{errors.__databaseOperation}</small> : null}</section>
    {operation && operation !== 'create' ? <section className={`binding-section database-query-section${collapsedSections.query ? ' is-collapsed' : ''}`}><div className="database-section-heading"><SectionTitle title="查询条件" /><div className="database-query-actions">{editable && !(draft.databaseConditions || []).length ? <Button className="database-condition-toggle" disabled={busy} icon={<PlusOutlined />} onClick={addConditionRow}>添加固定条件</Button> : null}{renderSectionToggle('query', '查询条件')}</div></div>
      {!collapsedSections.query ? <div className="database-section-content"><div className="database-condition-group database-request-condition-group">
        <div className="database-condition-group-heading"><SectionTitle level="group" title="请求参数条件" /></div>
        {queryFields.length ? <div className={`database-filter-table${operation === 'update' ? ' has-usage' : ''}`}>
          <div className="database-filter-table-head"><span>接口入参</span><span>条件类型</span><span>数据库字段</span>{operation === 'update' ? <span>字段用途</span> : null}</div>
          {queryFields.map((field) => renderField(field, 'filter'))}
        </div> : <Typography.Text type="secondary">暂无请求参数查询条件</Typography.Text>}
      </div>
      {(draft.databaseConditions || []).length ? <div className="database-condition-group database-fixed-condition-group">
        <div className="database-condition-group-heading"><SectionTitle level="group" title="固定条件" />{editable ? <Button className="database-condition-toggle" disabled={busy} icon={<PlusOutlined />} onClick={addConditionRow}>添加固定条件</Button> : null}</div>
        <div className="database-fixed-table">
          <div className="database-fixed-table-head"><span>数据库字段</span><span>条件类型</span><span>固定值</span></div>
          {(draft.databaseConditions || []).map((condition, index) => {
            const inputKey = String(index)
            const rawValue = conditionValueInputs[inputKey] ?? conditionValueInput(condition.value)
            return <div className="database-condition-row" key={`${index}:${condition.column}`}>
              {readOnly ? renderColumnSummary({ name: condition.column, type: condition.type, description: condition.description }) : <Select className="database-condition-column" showSearch optionFilterProp="label" disabled={!editable || busy} placeholder="选择数据库列" value={condition.column || undefined} aria-label={`固定条件数据库字段 ${index + 1}`} options={columns.filter((column) => column.name === condition.column || !(draft.databaseConditions || []).some((item, rowIndex) => rowIndex !== index && item.column === column.name)).map((column) => ({ value: column.name, label: `${column.name} · ${column.type}` }))} onChange={(columnName) => {
                setConditionValueInputs((current) => { const next = { ...current }; delete next[inputKey]; return next })
                updateCondition(index, columnName, 'eq', '')
              }} />}
              {readOnly ? <span className="database-static-operator">{CONDITION_OPERATOR_LABELS[condition.operator]}</span> : <Select className="database-condition-operator" disabled={!editable || busy || !condition.column} value={condition.operator} aria-label={`固定条件类型 ${index + 1}`} options={allowedDatabaseConditionOperators(condition.type).map((value) => ({ value, label: CONDITION_OPERATOR_LABELS[value] }))} onChange={(operator) => {
                const nextRawValue = operator === 'is_null' || operator === 'is_not_null' ? '' : rawValue
                setConditionValueInputs((current) => ({ ...current, [inputKey]: nextRawValue }))
                updateCondition(index, condition.column, operator, nextRawValue)
              }} />}
              <div className="database-condition-value-cell">
                {readOnly ? <span className="database-static-value" title={conditionValueInput(condition.value)}>{condition.operator === 'is_null' || condition.operator === 'is_not_null' ? '—' : conditionValueInput(condition.value)}</span> : renderConditionValueEditor(condition.type, condition.operator, rawValue, (value) => {
                  setConditionValueInputs((current) => {
                    const next = { ...current }
                    if (typeFamily(condition.type) === 'boolean') delete next[inputKey]
                    else next[inputKey] = value
                    return next
                  })
                  updateCondition(index, condition.column, condition.operator, value)
                }, !editable || busy || !condition.column, () => setConditionValueInputs((current) => { const next = { ...current }; delete next[inputKey]; return next }))}
                {!readOnly ? <Button disabled={!editable || busy} danger type="text" icon={<DeleteOutlined />} aria-label={`删除固定条件 ${index + 1}`} onClick={() => removeCondition(index)} /> : null}
              </div>
            </div>
          })}
        </div>
      </div> : null}</div> : null}
    </section> : null}
    {operation === 'create' || operation === 'update' ? <section className={`binding-section database-write-section${collapsedSections.write ? ' is-collapsed' : ''}`}><div className="database-section-heading"><SectionTitle title="写入字段" />{renderSectionToggle('write', '写入字段')}</div>{!collapsedSections.write ? <div className="database-section-content">{writeFields.map((field) => renderField(field, 'write'))}{!writeFields.length ? <Typography.Text type="secondary">暂无写入字段</Typography.Text> : null}</div> : null}</section> : null}
    <section className={`binding-section database-return-section${collapsedSections.return ? ' is-collapsed' : ''}`}><div className="database-section-heading"><div className="database-return-title"><SectionTitle title="返回字段" /><span className="database-return-count" aria-label={`${responseFields.length} 个返回字段`}>{responseFields.length}</span></div>{renderSectionToggle('return', '返回字段')}</div>{!collapsedSections.return ? <div className="database-section-content">{responseFields.length ? <div className="database-return-table"><div className="database-return-table-head"><span>响应字段</span><span>数据库字段</span></div>{responseFields.map((field) => renderField(field, 'read'))}</div> : <Typography.Text type="secondary">无返回字段</Typography.Text>}</div> : null}</section>
  </div>
}
