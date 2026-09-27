import type { WorkflowApiDatabaseCondition, WorkflowApiDatabaseQuery } from '../../../../typings'
import type { BindingSelection } from '../../../../typings/endpointDesign'

/** 为当前数据表创建尚待选择列和右值的查询条件。 */
export function emptyQueryCondition(selection: BindingSelection & { sourceType: 'database' }): WorkflowApiDatabaseCondition {
  return { kind: 'condition', sourceType: 'database', sourceId: selection.sourceId, schema: selection.schema,
    table: selection.table, column: '', type: '', operator: 'eq' }
}

/** 向顶层或指定子组追加查询条件。 */
export function addQueryCondition(query: WorkflowApiDatabaseQuery | undefined, condition: WorkflowApiDatabaseCondition, groupIndex?: number): WorkflowApiDatabaseQuery {
  const current = query || { join: 'and' as const, items: [] }
  if (groupIndex === undefined) return { ...current, items: [...current.items, condition] }
  return { ...current, items: current.items.map((item, index) => index === groupIndex && item.kind === 'group'
    ? { ...item, items: [...item.items, condition] } : item) }
}

/** 在顶层追加一个空分组，待用户填写后才能正式确认。 */
export function addQueryGroup(query: WorkflowApiDatabaseQuery | undefined): WorkflowApiDatabaseQuery {
  const current = query || { join: 'and' as const, items: [] }
  return { ...current, items: [...current.items, { kind: 'group', join: 'or', items: [] }] }
}

/** 更新顶层或子组中的一条查询条件。 */
export function replaceQueryCondition(query: WorkflowApiDatabaseQuery, index: number, condition: WorkflowApiDatabaseCondition, groupIndex?: number): WorkflowApiDatabaseQuery {
  if (groupIndex === undefined) return { ...query, items: query.items.map((item, position) => position === index ? condition : item) }
  return { ...query, items: query.items.map((item, position) => position === groupIndex && item.kind === 'group'
    ? { ...item, items: item.items.map((child, childIndex) => childIndex === index ? condition : child) } : item) }
}

/** 删除一项并在顶层清空后移除整棵查询树。 */
export function removeQueryItem(query: WorkflowApiDatabaseQuery, index: number, groupIndex?: number): WorkflowApiDatabaseQuery | undefined {
  const items = groupIndex === undefined ? query.items.filter((_, position) => position !== index)
    : query.items.map((item, position) => position === groupIndex && item.kind === 'group'
      ? { ...item, items: item.items.filter((_, childIndex) => childIndex !== index) } : item)
  return items.length ? { ...query, items } : undefined
}
