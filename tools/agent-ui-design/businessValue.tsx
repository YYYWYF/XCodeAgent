import type { ReactElement } from 'react'

/** 诊断身份由开发观察负责，普通结果卡不呈现内部标识或元数据。 */
const internal = /^(schemaVersion|agentId|threadId|runId|interactionId|metadata|diagnostics|checkpoint|traceId)$/i

/** 判断结果是否含可展示内容；保留 false 和 0，忽略空容器及内部诊断字段。 */
export function hasBusinessValue(value: unknown): boolean {
  if (value === undefined || value === null || value === '') return false
  if (Array.isArray(value)) return value.length > 0
  return typeof value !== 'object' || Object.keys(value).some((name) => !internal.test(name))
}

/** 按正式 Schema 标题展示业务值；无标题时保留业务字段名，不猜测订单等领域语义。 */
export function BusinessValue({ value, schema, depth = 0 }:
  { value: unknown; schema?: Record<string, unknown>; depth?: number }): ReactElement | null {
  if (value === undefined || value === null) return depth ? <span>无</span> : null
  if (depth > 5) return <span>内容较多，请查看业务页面。</span>
  if (typeof value !== 'object') return <span>{typeof value === 'boolean' ? value ? '是' : '否' : String(value)}</span>
  if (Array.isArray(value)) return <ul>{value.slice(0, 30).map((item, index) => <li key={index}>
    <BusinessValue value={item} schema={schema?.items as Record<string, unknown> | undefined} depth={depth + 1} /></li>)}</ul>
  const properties = schema?.properties as Record<string, Record<string, unknown>> | undefined
  return <dl>{Object.entries(value).filter(([name]) => !internal.test(name)).slice(0, 30).map(([name, item]) =>
    <div key={name}><dt>{typeof properties?.[name]?.title === 'string' ? String(properties[name].title) : name}：</dt><dd>
      <BusinessValue value={item} schema={properties?.[name]} depth={depth + 1} /></dd></div>)}</dl>
}
