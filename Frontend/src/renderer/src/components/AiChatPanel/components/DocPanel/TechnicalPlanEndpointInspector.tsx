import { QuestionCircleOutlined } from '@ant-design/icons'
import { Tooltip, Typography } from 'antd'
import type { ReactElement, ReactNode } from 'react'
import { cx } from '../../../../utils'
import {
  asRecord,
  authenticationLabel,
  methodClass,
  recordItems,
  schemaFor,
  stringItems,
  textValue,
  type JsonRecord
} from './TechnicalPlanDocPanelData'
import { SchemaTree } from './TechnicalPlanSchemaTree'
import './TechnicalPlanEndpointInspector.less'

const { Text } = Typography

/** 参数与正文共用类型树列标题，参数表为每层字段保留默认值列。 */
function SchemaTable({
  children,
  showDefault = false
}: {
  children: ReactNode
  showDefault?: boolean
}): ReactElement {
  return (
    <div
      className={cx(
        'technical-plan-schema-table',
        showDefault && 'technical-plan-schema-with-default'
      )}
    >
      <div className={cx('technical-plan-schema-column-head')}>
        <span>字段名</span>
        <span>类型</span>
        <span>必填</span>
        {showDefault ? <span>默认值</span> : null}
        <span>说明</span>
      </div>
      {children}
    </div>
  )
}

/** 标题旁提供可悬停、聚焦或点击的说明入口，正文默认保持简洁。 */
function SchemaHeading({ label, help }: { label: string; help?: string }): ReactElement {
  return (
    <div className={cx('technical-plan-schema-heading')}>
      <strong>{label}</strong>
      {help ? (
        <Tooltip
          title={help}
          trigger={['hover', 'focus', 'click']}
          overlayClassName={cx('technical-plan-transport-tooltip')}
        >
          <button
            type="button"
            className={cx('technical-plan-transport-help')}
            aria-label={`${label}如何发送`}
          >
            <QuestionCircleOutlined aria-hidden="true" />
          </button>
        </Tooltip>
      ) : null}
    </div>
  )
}

/** 根据实际参数位置解释发送方式，URL 示例仅用占位值，不推测真实业务取值。 */
function parameterTransportHelp(location: string, parameters: JsonRecord[], path: string): string {
  const names = parameters.map((parameter) => textValue(parameter.name))
  if (location === 'query') {
    return `query：拼在 URL 的 ? 后，多个参数用 & 连接。\n例如：${path}?${names.map((name) => `${name}=值`).join('&')}`
  }
  if (location === 'path') {
    return `path：替换 URL 路径中的占位符后发送。\n例如：${path} 中的 ${names.map((name) => `{${name}}`).join('、')} 会替换为实际值。`
  }
  if (location === 'header') {
    return `header：放在 HTTP 请求头中发送。\n例如：${names[0]}: 值`
  }
  return `cookie：随 HTTP 请求头中的 Cookie 发送。\n例如：${names.map((name) => `${name}=值`).join('; ')}`
}

/** 独立展示请求体或响应体，缺少引用与引用不可读分别使用原位空态。 */
function BodySchema({
  label,
  reference,
  contract,
  help
}: {
  label: string
  reference: string
  contract: JsonRecord
  help?: string
}): ReactElement {
  const schema = schemaFor(contract, reference)
  return (
    <section className={cx('technical-plan-schema-card')} aria-label={label}>
      <SchemaHeading label={label} help={help} />
      {!reference ? (
        <Text type="secondary">无{label}</Text>
      ) : Object.keys(schema).length ? (
        <SchemaTable>
          <SchemaTree name={reference} schema={schema} schemas={asRecord(contract.schemas)} />
        </SchemaTable>
      ) : (
        <Text type="secondary">{reference} · 类型定义暂不可读取</Text>
      )}
    </section>
  )
}

/** 完整展示接口参数、请求体和响应体，各自独立解析同一 Contract 内的类型。 */
export function EndpointInspector({
  contract,
  endpoint
}: {
  contract: JsonRecord
  endpoint: JsonRecord
}): ReactElement {
  const parameters = recordItems(endpoint.parameters)
  // 参数位置只在组标题展示一次，避免每个字段重复标注 query/path/header。
  const parameterGroups = new Map<string, JsonRecord[]>()
  for (const parameter of parameters) {
    const location = textValue(parameter.in, 'query')
    const group = parameterGroups.get(location) || []
    group.push(parameter)
    parameterGroups.set(location, group)
  }
  const schemas = asRecord(contract.schemas)
  const errorCodes = stringItems(endpoint.error_codes)
  const method = textValue(endpoint.method, 'GET').toUpperCase()
  const path = textValue(endpoint.path, '/')
  const requestReference = textValue(endpoint.request_schema_ref).trim()
  const parameterHelp = Array.from(parameterGroups, ([location, group]) =>
    parameterTransportHelp(location, group, path)
  ).join('\n\n')
  return (
    <div className={cx('technical-plan-inspector', 'technical-plan-complete-types')}>
      <div className={cx('technical-plan-inspector-title')}>
        <span className={cx('technical-plan-method', methodClass(method))}>{method}</span>
        <strong className={cx('technical-plan-endpoint-name')}>
          {textValue(endpoint.name, '未命名接口')}
        </strong>
        <code>{path}</code>
      </div>
      {textValue(endpoint.summary) ? <Text>{textValue(endpoint.summary)}</Text> : null}
      <dl className={cx('technical-plan-inspector-grid')}>
        <div>
          <dt>鉴权</dt>
          <dd>{authenticationLabel(endpoint.authentication || contract.authentication)}</dd>
        </div>
        <div>
          <dt>错误码</dt>
          <dd>{errorCodes.length ? errorCodes.join('、') : '未声明'}</dd>
        </div>
      </dl>
      <Text type="secondary">以下为 API 契约类型（JSON Schema），数据库列类型单独定义。</Text>
      {parameters.length ? (
        <section className={cx('technical-plan-schema-card')} aria-label="请求参数">
          <SchemaHeading label="请求参数" help={parameterHelp} />
          {Array.from(parameterGroups, ([location, group]) => (
            <div className={cx('technical-plan-parameter-group')} key={location}>
              <span className={cx('technical-plan-parameter-location')}>{location}</span>
              <SchemaTable showDefault>
                {group.map((parameter, index) => (
                  <SchemaTree
                    key={`${textValue(parameter.name)}:${index}`}
                    name={textValue(parameter.name, `参数 ${index + 1}`)}
                    schema={asRecord(parameter.schema)}
                    description={textValue(parameter.description)}
                    schemas={schemas}
                    isField
                    showDefault
                    fieldRequired={Boolean(parameter.required)}
                    fieldOptional={!parameter.required}
                  />
                ))}
              </SchemaTable>
            </div>
          ))}
        </section>
      ) : null}
      {requestReference ? (
        <BodySchema
          label="请求体"
          reference={requestReference}
          contract={contract}
          help="作为 JSON 数据放在 Body 中发送。"
        />
      ) : null}
      <BodySchema
        label="响应体"
        reference={textValue(endpoint.response_schema_ref)}
        contract={contract}
      />
    </div>
  )
}
