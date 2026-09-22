import { Select } from 'antd'
import type { ReactElement } from 'react'
import {
  CONDITION_OPERATORS,
  conditionSqlFragment,
  contractFieldLabel,
  mappingSourceName,
  type BindingDraft
} from '../../../AppApis/model'
import { cx } from '../../../../utils'
import type { FieldMappingContext } from './types'

type Props = {
  /** 契约上下文：表列候选、契约出入参与模板操作类型。 */
  context: FieldMappingContext
  /** 共享草稿：条件行、写入行、返回字段映射与排序。 */
  draft: BindingDraft
  /** 提交中锁定：下拉禁用。 */
  locked: boolean
  /** 草稿局部更新：与连线画布共用同一份草稿正本。 */
  onChange: (patch: Partial<BindingDraft>) => void
}

/**
 * 数据库绑定的模板编辑器：查询/删除条件行、新增/修改写入行、查询返回字段映射
 * 与 SQL 预览四张分节卡。与外部映射（ExternalMapping 的连线画布）对称——
 * 由 index.tsx 按 context.kind 分发，草稿正本仍由面板层持有。
 */
export default function DatabaseMapping({ context, draft, locked, onChange }: Props): ReactElement {
  const columnOptions = context.columns.map((column) => ({
    value: column.name,
    label: `${column.name}（${column.comment}）`
  }))
  const operatorOptions = CONDITION_OPERATORS.map((value) => ({ value }))
  /** 契约入参展示标签：按参数名回查英文标识。 */
  const inputLabel = (name: string): string => {
    const param = context.inputParams.find((item) => item.name === name)
    return contractFieldLabel(param?.code || '', name)
  }
  /** 契约出参展示标签：按出参名回查英文标识。 */
  const outputLabel = (name: string): string => {
    const output = context.outputs.find((item) => item.name === name)
    return contractFieldLabel(output?.code || '', name)
  }
  /** 更新模板条件行的落列或操作符。 */
  const updateCondition = (
    index: number,
    patch: Partial<BindingDraft['conditions'][number]>
  ): void => {
    onChange({
      conditions: draft.conditions.map((row, rowIndex) =>
        rowIndex === index ? { ...row, ...patch } : row
      )
    })
  }
  /** 更新模板写入行的落列。 */
  const updateSetter = (index: number, patch: Partial<BindingDraft['setters'][number]>): void => {
    onChange({
      setters: draft.setters.map((row, rowIndex) => (rowIndex === index ? { ...row, ...patch } : row))
    })
  }
  /** 选定某出参的来源字段：以「name（说明）」写入映射标签。 */
  const updateMapping = (field: string, sourceField: string): void => {
    const column = context.columns.find((item) => item.name === sourceField)
    if (!column) return
    onChange({
      mappings: draft.mappings.map((row) =>
        row.field === field
          ? {
              ...row,
              matched: true,
              matchType: 'manual' as const,
              sourceLabel: `${column.name}（${column.comment}）`
            }
          : row
      )
    })
  }

  return (
    <>
      {(context.op === '查询' || context.op === '删除') && draft.conditions.length > 0 && (
        <section className={cx('field-mapping-card')}>
          <div className={cx('field-mapping-card-title')}>
            {context.op === '查询' ? '查询条件' : '定位条件'}
          </div>
          {draft.conditions.map((row, index) => (
            <div
              key={`${row.param}-${index}`}
              className={cx('field-mapping-row', 'row-condition', row.fixed && 'row-fixed')}
            >
              <span className={cx('field-mapping-param')} title={inputLabel(row.param)}>
                <em>{row.fixed ? '登录用户' : '入参'}</em>
                <code>{row.fixed ? row.param : inputLabel(row.param)}</code>
              </span>
              <Select
                aria-label={`${row.param} 条件操作符`}
                className={cx('field-mapping-operator')}
                disabled={locked}
                options={operatorOptions}
                value={row.operator}
                onChange={(operator) => updateCondition(index, { operator })}
              />
              <Select
                aria-label={`${row.param} 条件落列`}
                className={cx('field-mapping-field')}
                disabled={locked}
                options={columnOptions}
                placeholder="选择落列"
                value={row.column || undefined}
                onChange={(column) => {
                  const columnInfo = context.columns.find((item) => item.name === column)
                  updateCondition(index, { column, columnComment: columnInfo?.comment || '' })
                }}
              />
            </div>
          ))}
        </section>
      )}
      {(context.op === '新增' || context.op === '修改') && draft.setters.length > 0 && (
        <section className={cx('field-mapping-card')}>
          <div className={cx('field-mapping-card-title')}>写入字段</div>
          {draft.setters.map((row, index) => (
            <div key={`${row.param}-${index}`} className={cx('field-mapping-row', 'row-mapping')}>
              <span className={cx('field-mapping-param')} title={inputLabel(row.param)}>
                <em>入参</em>
                <code>{inputLabel(row.param)}</code>
                {row.required ? <em>必填</em> : null}
              </span>
              <span className={cx('field-mapping-arrow')}>→</span>
              <Select
                aria-label={`${row.param} 写入列`}
                className={cx('field-mapping-field')}
                disabled={locked}
                options={columnOptions}
                placeholder="选择写入列"
                value={row.column || undefined}
                onChange={(column) => {
                  const columnInfo = context.columns.find((item) => item.name === column)
                  updateSetter(index, { column, columnComment: columnInfo?.comment || '' })
                }}
              />
            </div>
          ))}
        </section>
      )}
      {context.op === '查询' && (
        <section className={cx('field-mapping-card')}>
          <div className={cx('field-mapping-card-title')}>返回字段</div>
          {draft.mappings.map((mapping) => (
            <div
              key={mapping.field}
              className={cx('field-mapping-row', 'row-mapping', !mapping.matched && 'row-pending')}
            >
              <span className={cx('field-mapping-param')} title={outputLabel(mapping.field)}>
                <em>出参</em>
                <code>{outputLabel(mapping.field)}</code>
              </span>
              <span className={cx('field-mapping-arrow')}>←</span>
              <Select
                aria-label={`选择 ${mapping.field} 的来源列`}
                className={cx('field-mapping-field')}
                disabled={locked}
                options={columnOptions}
                placeholder={mapping.matched ? undefined : '选择来源列'}
                value={mapping.matched ? mappingSourceName(mapping.sourceLabel) : undefined}
                onChange={(source) => updateMapping(mapping.field, source)}
              />
            </div>
          ))}
        </section>
      )}
      {context.op && (
        <section className={cx('field-mapping-card')}>
          <div className={cx('field-mapping-card-title')}>SQL 预览</div>
          <pre className={cx('field-mapping-preview')}>
            <code>{buildSqlPreview(draft, context.targetName)}</code>
          </pre>
        </section>
      )}
    </>
  )
}

/**
 * 由当前草稿实时生成模板 SQL 预览（数据库绑定）：与适配代码生成器共用 conditionSqlFragment，
 * 「表字段 + 操作符 + 取值来源」的每处改动都即时反映到语句上，是表绑定的闭环结果。
 */
function buildSqlPreview(draft: BindingDraft, table: string): string {
  const mappedSetters = draft.setters.filter((row) => row.column)
  const whereLines = draft.conditions.map((row, index) => {
    const column = row.column || '?'
    const fragment = conditionSqlFragment(
      column,
      row.operator,
      row.fixed ? ':currentUser' : `:${column}`
    )
    return `${index === 0 ? 'WHERE' : '  AND'} ${fragment}`
  })
  if (draft.op === '删除') {
    return [`DELETE FROM ${table}`, ...whereLines].join('\n')
  }
  if (draft.op === '新增') {
    const insertColumns = mappedSetters.map((row) => row.column).join(', ')
    const insertValues = mappedSetters.map((row) => `:${row.column}`).join(', ')
    return `INSERT INTO ${table} (${insertColumns})\nVALUES (${insertValues})`
  }
  if (draft.op === '修改') {
    const setList = mappedSetters.map((row) => `${row.column} = :${row.column}`).join(', ')
    return [`UPDATE ${table}`, `SET ${setList}`, ...whereLines].join('\n')
  }
  const selectList = draft.mappings
    .map((row) => (row.matched ? mappingSourceName(row.sourceLabel) : `/* ${row.field} 未映射 */`))
    .join(', ')
  const lines = [`SELECT ${selectList}`, `FROM ${table}`, ...whereLines]
  if (draft.orderBy) lines.push(`ORDER BY ${draft.orderBy}`)
  return lines.join('\n')
}
