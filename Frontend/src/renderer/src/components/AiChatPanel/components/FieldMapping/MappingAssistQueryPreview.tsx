import { Fragment, type ReactElement } from 'react'
import type { WorkflowApiDatabaseCondition, WorkflowApiDatabaseQuery } from '../../../../typings'
import ReadOnlyValue from './ReadOnlyValue'
import MappingAssistReview from './MappingAssistReview'
import { DATABASE_CONDITION_LABELS } from './databaseQueryModel'

/** 用单组表头和紧凑的关系分组展示查询树，不提供逐条件勾选。 */
export default function MappingAssistQueryPreview({ query }: { query?: WorkflowApiDatabaseQuery | null }): ReactElement {
  /** 保留字段类型、运算符及完整右值，空值判断明确显示无需取值。 */
  const conditionRow = (condition: WorkflowApiDatabaseCondition, key: string): ReactElement => <div className="mapping-review-query-row" key={key}>
    <div><code>{condition.column}</code>{condition.description && <span className="mapping-assist-secondary">（{condition.description}）</span>}<small className="mapping-assist-secondary">{condition.type}</small></div>
    <span className="mapping-assist-secondary">{DATABASE_CONDITION_LABELS[condition.operator]}</span>
    {condition.operator === 'is_null' || condition.operator === 'is_not_null' ? <span className="mapping-review-no-value">无需取值</span>
      : <ReadOnlyValue title={condition.column} right={condition.right} />}
  </div>
  return <section aria-label="生成的查询条件">
    {!query?.items.length ? <p className="mapping-assist-secondary">尚未配置查询条件</p> : <>
      <div className="mapping-review-section-heading"><strong>{query.join === 'and' ? '满足全部条件' : '满足任一条件'} <span className="mapping-review-join">{query.join.toUpperCase()}</span></strong></div>
      <div className="mapping-review-query-head"><span>字段</span><span>条件</span><span>值来源</span><span>取值内容</span></div>
      <div className="mapping-review-query-tree">{query.items.map((item, index) => <Fragment key={index}>
        {index > 0 && <div className="mapping-review-connector">{query.join === 'and' ? '且 AND' : '或 OR'}</div>}
        {item.kind === 'condition' ? conditionRow(item, String(index)) : <section className="mapping-review-query-group" aria-label={`第 ${index + 1} 个条件组 ${item.join.toUpperCase()}`}>
          <div className="mapping-review-group-label">{item.join === 'and' ? '以下全部满足' : '以下任一满足'} · {item.join.toUpperCase()}</div>
          {item.items.length ? item.items.map((condition, childIndex) => conditionRow(condition, `${index}:${childIndex}`)) : <p className="mapping-assist-secondary">该条件组尚未配置</p>}
        </section>}
      </Fragment>)}</div>
    </>}
    <MappingAssistReview area="query" target="databaseQuery" />
  </section>
}
