import { Tabs } from 'antd'
import type { DebugEndpoint } from '../../../../service/runtimeWorkbench'
import { debugObject, debugStringMap } from '../../../../service/runtimeDebugRequest'
import type { DebugRequestValues } from '../../../../service/runtimeDebugRequest'
import { checkDebugJson } from '../../../../service/debugJsonValidation'
import { debugRequestPath } from '../../../../service/runtimeDebugRequest'
import DebugJsonEditor from './DebugJsonEditor'
import DebugKeyValueTable from './DebugKeyValueTable'
import { debugKeyValueMap, debugKeyValueRows } from '../../../../service/debugKeyValue'
import type { DebugKeyValueRow } from '../../../../service/debugKeyValue'

export type DebugRequestFields = { pathParameters: DebugKeyValueRow[]; query: DebugKeyValueRow[]; headers: DebugKeyValueRow[]; context: string }

/** 从当前接口契约生成参数编辑初值，不生成业务请求或填写用户身份。 */
export function initialDebugFields(endpoint?: DebugEndpoint): DebugRequestFields {
  const paths = Object.fromEntries([...(endpoint?.path || '').matchAll(/\{([^}:]+)(?::[^}]+)?\}/g)].map((match) => [match[1], '']))
  const query = Object.fromEntries((endpoint?.parameters || []).filter((item) => item.in === 'query' && item.required).map((item) => [item.name, '']))
  return { pathParameters: debugKeyValueRows(paths), query: debugKeyValueRows(query), headers: [], context: '{}' }
}

/** 解析业务请求配置；页面仅是可选上下文，不能覆盖正式交互或输入字段。 */
export function readDebugFields(value: DebugRequestFields): DebugRequestValues {
  const context = debugObject(value.context, '上下文参数')
  if (Object.keys(context).some((key) => !['pageId', 'actionId', 'contextItems'].includes(key)))
    throw new Error('上下文仅支持可选的 pageId、actionId、contextItems。')
  return { pathParameters: debugKeyValueMap(value.pathParameters, '路径参数'), query: debugKeyValueMap(value.query, '查询参数'),
    headers: debugStringMap(JSON.stringify(debugKeyValueMap(value.headers, 'Headers')), 'Headers'), context }
}

/** 单个请求字段按语法、平台 Header 边界和契约参数名校验，错误可直接出现在编辑器下方。 */
export function validateDebugField(endpoint: DebugEndpoint, key: keyof DebugRequestFields, rows: DebugRequestFields[keyof DebugRequestFields]): void {
  if (key === 'context') {
    const checked = checkDebugJson(rows as string, { objectOnly: true })
    if (checked.errors.length) throw new Error(checked.errors[0])
    const value = checked.value as Record<string, unknown>
    if (Object.keys(value).some((name) => !['pageId', 'actionId', 'contextItems'].includes(name))) throw new Error('上下文仅支持 pageId、actionId、contextItems。')
    return
  }
  const value = debugKeyValueMap(rows as DebugKeyValueRow[], key === 'headers' ? 'Headers' : '参数')
  if (key === 'headers') {
    debugStringMap(JSON.stringify(value), 'Headers')
    if (endpoint.responseMode === 'ag-ui' && Object.keys(value).some((name) => name.toLowerCase() !== 'traceparent')) throw new Error('此流式接口可配置 traceparent，其他 Header 由平台管理。')
    return
  }
  const names = key === 'pathParameters' ? [...endpoint.path.matchAll(/\{([^}:]+)(?::[^}]+)?\}/g)].map((match) => match[1])
    : endpoint.parameters.filter((item) => item.in === 'query').map((item) => item.name)
  if (Object.keys(value).some((name) => !names.includes(name))) throw new Error('包含接口契约未声明的参数。')
  const required = key === 'pathParameters' ? names : endpoint.parameters.filter((item) => item.in === 'query' && item.required).map((item) => item.name)
  const missing = required.filter((name) => !value[name])
  if (missing.length) throw new Error(`请填写必填参数：${missing.join('、')}`)
}

/** 编辑态和发送前共用同一校验，语法正确但不符合接口 Schema 的 Body 同样阻止发送。 */
export function debugRequestIssues(endpoint: DebugEndpoint, fields: DebugRequestFields, body: string, validateBody: boolean): string[] {
  const errors: string[] = []
  for (const key of ['pathParameters', 'query', 'headers', ...(endpoint.responseMode === 'ag-ui' ? ['context'] : [])] as (keyof DebugRequestFields)[]) {
    try { validateDebugField(endpoint, key, fields[key]) } catch (reason) { errors.push(reason instanceof Error ? reason.message : '请求参数无效。') }
  }
  if (validateBody) {
    const result = checkDebugJson(body, { schema: endpoint.requestSchema, objectOnly: endpoint.responseMode === 'ag-ui' })
    errors.push(...result.errors, ...(result.validatorError ? [result.validatorError] : []))
  }
  if (!errors.length) {
    try { debugRequestPath(endpoint, readDebugFields(fields)) } catch (reason) { errors.push(reason instanceof Error ? reason.message : '请求参数无效。') }
  }
  return errors
}

/** 请求参数与协议详情独立展示，JSON 接口提供请求体编辑，AG-UI 使用现有消息/任务输入。 */
export default function AgentDebugRequestEditor({ endpoint, value, onChange, disabled, body, onBodyChange }: {
  endpoint: DebugEndpoint; value: DebugRequestFields; onChange: (value: DebugRequestFields) => void
  disabled: boolean; body: string; onBodyChange: (body: string) => void
}): JSX.Element {
  /** 参数和 Headers 使用项目现有表格组件，JSON 仅用于业务 Body 与高级结构化上下文。 */
  const field = (key: Exclude<keyof DebugRequestFields, 'context'>, label: string): JSX.Element => <DebugKeyValueTable label={label}
    rows={value[key]} disabled={disabled} fixedKeys={key === 'pathParameters'} validate={() => validateDebugField(endpoint, key, value[key])}
    onChange={(rows) => onChange({ ...value, [key]: rows })} />
  const json = endpoint.responseMode === 'json'
  return <section className="agent-debug-request-config" aria-label="请求编辑区">
    <Tabs size="small" defaultActiveKey={json && !['GET', 'HEAD'].includes(endpoint.method) ? 'body' : 'parameters'} items={[
      { key: 'parameters', label: '参数', children: <>
        {endpoint.path.includes('{') && field('pathParameters', '路径参数')}
        {endpoint.parameters.some((item) => item.in === 'query') ? <>{field('query', '查询参数')}
          <p>契约参数：{endpoint.parameters.filter((item) => item.in === 'query').map((item) => `${item.name}${item.required ? '（必填）' : ''}`).join('、')}。参数值使用字符串。</p></>
          : !endpoint.path.includes('{') && <div className="agent-debug-inline-empty">此接口没有路径或查询参数。</div>}
      </> },
      { key: 'headers', label: 'Headers', children: <>{field('headers', 'Headers')}
        <p>{endpoint.responseMode === 'ag-ui' ? '可设置 traceparent；调试身份、Content-Type 和 Accept 由平台管理。'
          : '可填写业务 Header；调试身份、Cookie、Content-Type 和 Accept 由平台管理。'}</p></> },
      ...(json ? [{ key: 'body', label: 'Body', children:
          ['GET', 'HEAD'].includes(endpoint.method) ? <div className="agent-debug-inline-empty">此接口使用路径和查询参数，无请求体。</div>
            : <DebugJsonEditor label="JSON 请求体" value={body} disabled={disabled} schema={endpoint.requestSchema}
              onChange={onBodyChange} /> }]
        : [{ key: 'context', label: '上下文（可选）', children: <><DebugJsonEditor label="上下文参数（JSON 对象）" compact objectOnly
          value={value.context} disabled={disabled} onChange={(context) => onChange({ ...value, context })}
          validate={() => validateDebugField(endpoint, 'context', value.context)} />
          <p>默认不携带页面。需要模拟页面来源时，可填写 pageId、actionId、contextItems。</p></> }]),
      { key: 'schema', label: '接口契约', children: <><strong>业务输入 Schema</strong><pre>{JSON.stringify(endpoint.requestSchema, null, 2)}</pre>
        <strong>业务结果 Schema</strong><pre>{JSON.stringify(endpoint.responseSchema, null, 2)}</pre></> }
    ]} />
  </section>
}
