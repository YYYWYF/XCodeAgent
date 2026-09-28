import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import { Button, Select, Typography } from 'antd'
import { useState } from 'react'
import type { ReactElement } from 'react'
import type { WorkflowApiDatabaseCondition, WorkflowApiDatabaseConditionOperator, WorkflowApiDatabaseQuery, WorkflowApiField } from '../../../../typings'
import type { BindingSelection } from '../../../../typings/endpointDesign'
import { allowedDatabaseConditionOperators, apiDesignFieldKey, queryParameterCompatible } from '../WorkflowRunCard/apiDesignSerialization'
import { addQueryCondition, addQueryGroup, emptyQueryCondition, removeQueryItem, replaceQueryCondition } from './databaseQueryModel'
import ValueRuleEditor from './ValueRuleEditor'
import './DatabaseQueryEditor.less'

const OPERATOR_LABELS: Record<WorkflowApiDatabaseConditionOperator, string> = {
  eq: '等于', ne: '不等于', gt: '大于', gte: '大于等于', lt: '小于', lte: '小于等于',
  contains: '包含', not_contains: '不包含', starts_with: '前缀匹配', ends_with: '后缀匹配',
  in: '属于', not_in: '不属于', between: '介于', not_between: '不介于', is_null: '为空', is_not_null: '不为空'
}

const QUERY_JOIN_OPTIONS = [
  { value: 'and', label: '全部条件 AND' },
  { value: 'or', label: '任一条件 OR' }
]

type Props = {
  selection: BindingSelection & { sourceType: 'database' }
  columns: Array<{ name: string; type: string; description?: string }>
  fields: WorkflowApiField[]
  query?: WorkflowApiDatabaseQuery
  editable: boolean
  readOnly: boolean
  busy: boolean
  error?: string
  onChange: (query?: WorkflowApiDatabaseQuery) => void
}

/** 渲染数据库列优先的查询条件与一层 AND/OR 分组。 */
export default function DatabaseQueryEditor({ selection, columns, fields, query, editable, readOnly, busy, error, onChange }: Props): ReactElement {
  const [rowRevision, setRowRevision] = useState(0)
  const requestFields = fields.filter((field) => field.side === 'request')
  const fieldByKey = new Map(requestFields.map((field) => [apiDesignFieldKey(field), field]))
  // 首个条件组之前保留普通条件行；从首个条件组起，后续顶层项共用连接主干。
  const firstConnectedIndex = query?.items.findIndex((item) => item.kind === 'group') ?? -1

  /** 替换一条查询条件，保持其所在分组和其他行不变。 */
  const update = (index: number, condition: WorkflowApiDatabaseCondition, groupIndex?: number): void => {
    if (query) onChange(replaceQueryCondition(query, index, condition, groupIndex))
  }

  /** 渲染一行条件及其按列类型约束的右值编辑器。 */
  const renderCondition = (condition: WorkflowApiDatabaseCondition, index: number, groupIndex?: number): ReactElement => {
    const key = groupIndex === undefined ? `root-${index}` : `group-${groupIndex}-${index}`
    const isNull = condition.operator === 'is_null' || condition.operator === 'is_not_null'
    const right = condition.right
    const endpointKey = right?.kind === 'endpoint' && right.endpointField ? apiDesignFieldKey(right.endpointField) : undefined
    const rightField = endpointKey ? fieldByKey.get(endpointKey) : undefined
    const operators: WorkflowApiDatabaseConditionOperator[] = condition.column
      ? (rightField ? allowedDatabaseConditionOperators(condition.type).filter((operator) => ['is_null', 'is_not_null'].includes(operator) || queryParameterCompatible(rightField.type, condition.type, operator)) : allowedDatabaseConditionOperators(condition.type))
      : ['eq']
    return <div className={`database-query-row${readOnly ? ' is-readonly' : ''}`} key={`${key}:${rowRevision}`}>
      <div className="database-query-column-cell">
        {readOnly ? <div className="database-query-readonly-field" title={`${condition.column}${condition.description ? `（${condition.description}）` : ''}`}><code>{condition.column}</code>{condition.description ? <span className="field-detail-meaning">（{condition.description}）</span> : null}<span className="field-detail-type">{condition.type}</span></div> : <Select className="database-query-column-select" showSearch optionFilterProp="label" placeholder="选择字段" disabled={!editable || busy} value={condition.column || undefined}
          options={columns.map((column) => ({ value: column.name, label: `${column.name}${column.description ? `（${column.description}）` : ''} · ${column.type}` }))}
          onChange={(name) => { const column = columns.find((item) => item.name === name); if (column) { update(index, { ...condition, column: name, type: column.type, description: column.description, operator: 'eq', right: undefined }, groupIndex) } }} />}
      </div>
      {readOnly ? <span className="database-static-operator">{OPERATOR_LABELS[condition.operator]}</span> : <Select disabled={!editable || busy || !condition.column} value={condition.operator} options={operators.map((operator) => ({ value: operator, label: OPERATOR_LABELS[operator] }))}
        onChange={(operator: WorkflowApiDatabaseConditionOperator) => { update(index, { ...condition, operator, right: operator === 'is_null' || operator === 'is_not_null' ? undefined : condition.right?.kind === 'fixed' ? { kind: 'fixed', value: undefined } : condition.right }, groupIndex) }} />}
      {isNull ? <span className="database-query-no-value">无需右值</span> : <>
        <ValueRuleEditor key={`${condition.column}:${condition.operator}`} title={condition.column || '查询条件'} right={condition.right} fields={fields} type={condition.type}
          collection={['in', 'not_in', 'between', 'not_between'].includes(condition.operator)} readOnly={readOnly}
          disabled={!editable || busy || !condition.column} onChange={(right) => update(index, { ...condition, right }, groupIndex)} />
      </>}
      {!readOnly ? <Button danger type="text" disabled={!editable || busy} icon={<DeleteOutlined />} aria-label="删除查询条件" onClick={() => { setRowRevision((value) => value + 1); if (query) onChange(removeQueryItem(query, index, groupIndex)) }} /> : null}
    </div>
  }

  /** 渲染顶层或分组内共享的四列表头。 */
  const renderTableHead = (): ReactElement => <div className="database-query-head"><span>字段</span><span>条件</span><span>值来源</span><span>{readOnly ? '取值内容' : '参数'}</span>{!readOnly ? <span aria-hidden="true" /> : null}</div>

  // 连续分组的首尾标记用于绘制一条完整主干，并让最后一个分叉在横线处结束。
  return <div className={`database-query-editor${readOnly ? ' is-readonly' : ''}`}>
    {query?.items.length ? <div className="database-query-scope">
      <div className="database-query-toolbar">
        <div className="database-query-satisfy"><span>满足以下</span>{readOnly ? <strong>{query.join.toUpperCase()}</strong> : <Select className="database-query-join" aria-label="顶层条件关系" disabled={!editable || busy} value={query.join} options={QUERY_JOIN_OPTIONS} onChange={(join) => onChange({ ...query, join })} />}</div>
        {!readOnly ? <div className="database-query-actions"><Button icon={<PlusOutlined />} disabled={!editable || busy} onClick={() => onChange(addQueryCondition(query, emptyQueryCondition(selection)))}>添加条件</Button><Button icon={<PlusOutlined />} disabled={!editable || busy} onClick={() => onChange(addQueryGroup(query))}>添加条件组</Button></div> : null}
      </div>
      <div className="database-query-list">
        {renderTableHead()}
        {query.items.map((item, index) => {
          const isConnected = firstConnectedIndex >= 0 && index >= firstConnectedIndex
          const nodeClassName = `database-query-connected-item ${item.kind === 'group' ? 'database-query-group' : 'database-query-condition-node'}${index === firstConnectedIndex ? ' is-first-node' : ''}${isConnected && index === query.items.length - 1 ? ' is-last-node' : ''}`
          if (item.kind === 'condition') {
            if (!isConnected) return renderCondition(item, index)
            return <div className={nodeClassName} key={`condition-card-${index}`}>
              <div className="database-query-group-card database-query-condition-card">
                <div className="database-query-group-list">{renderTableHead()}{renderCondition(item, index)}</div>
              </div>
            </div>
          }
          return <div className={nodeClassName} key={`group-${index}`}>
            <div className="database-query-group-card">
              <div className="database-query-group-toolbar">
                <div className="database-query-satisfy"><span>满足以下</span>{readOnly ? <strong>{item.join.toUpperCase()}</strong> : <Select className="database-query-join" aria-label={`第 ${index + 1} 个条件组关系`} disabled={!editable || busy} value={item.join} options={QUERY_JOIN_OPTIONS} onChange={(join) => onChange({ ...query, items: query.items.map((current, position) => position === index && current.kind === 'group' ? { ...current, join } : current) })} />}</div>
                {!readOnly ? <div className="database-query-actions"><Button icon={<PlusOutlined />} disabled={!editable || busy} onClick={() => onChange(addQueryCondition(query, emptyQueryCondition(selection), index))}>添加条件</Button><Button className="database-query-delete-group" danger type="text" disabled={!editable || busy} icon={<DeleteOutlined />} aria-label="删除条件组" onClick={() => { setRowRevision((value) => value + 1); onChange(removeQueryItem(query, index)) }} /></div> : null}
              </div>
              {item.items.length ? <div className="database-query-group-list">{renderTableHead()}{item.items.map((condition, childIndex) => renderCondition(condition, childIndex, index))}</div>
                : <div className="database-query-group-empty">该条件组还没有条件</div>}
            </div>
          </div>
        })}
      </div>
    </div> : <div className="database-query-empty">
      <Typography.Text type="secondary">暂无查询条件</Typography.Text>
      {!readOnly ? <div className="database-query-actions"><Button icon={<PlusOutlined />} disabled={!editable || busy} onClick={() => onChange(addQueryCondition(query, emptyQueryCondition(selection)))}>添加条件</Button><Button icon={<PlusOutlined />} disabled={!editable || busy} onClick={() => onChange(addQueryGroup(query))}>添加条件组</Button></div> : null}
    </div>}
    {error && !busy ? <small className="database-mapping-error">{error}</small> : null}
  </div>
}
