import { Typography } from 'antd'
import type { ReactElement } from 'react'
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

/** 独立展示请求体或响应体，缺少引用与引用不可读分别使用原位空态。 */
function BodySchema({
  label,
  reference,
  contract
}: {
  label: string
  reference: string
  contract: JsonRecord
}): ReactElement {
  const schema = schemaFor(contract, reference)
  return (
    <section className={cx('technical-plan-schema-card')} aria-label={label}>
      <div className={cx('technical-plan-schema-heading')}>
        <strong>{label}</strong>
      </div>
      {!reference ? (
        <Text type="secondary">无{label}</Text>
      ) : Object.keys(schema).length ? (
        <SchemaTree name={reference} schema={schema} schemas={asRecord(contract.schemas)} />
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
  const schemas = asRecord(contract.schemas)
  const errorCodes = stringItems(endpoint.error_codes)
  const method = textValue(endpoint.method, 'GET').toUpperCase()
  return (
    <div className={cx('technical-plan-inspector', 'technical-plan-complete-types')}>
      <div className={cx('technical-plan-inspector-title')}>
        <span className={cx('technical-plan-method', methodClass(method))}>{method}</span>
        <strong className={cx('technical-plan-endpoint-name')}>
          {textValue(endpoint.name, '未命名接口')}
        </strong>
        <code>{textValue(endpoint.path, '/')}</code>
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
      <section className={cx('technical-plan-schema-card')} aria-label="请求参数">
        <div className={cx('technical-plan-schema-heading')}>
          <strong>请求参数</strong>
        </div>
        {parameters.length ? (
          parameters.map((parameter, index) => (
            <div
              className={cx('technical-plan-parameter')}
              key={`${textValue(parameter.in)}:${textValue(parameter.name)}:${index}`}
            >
              <div className={cx('technical-plan-parameter-location')}>
                <span>{textValue(parameter.in, 'query')}</span>
                {parameter.required ? (
                  <span className={cx('technical-plan-required')}>必填</span>
                ) : (
                  <span>可选</span>
                )}
              </div>
              <SchemaTree
                name={textValue(parameter.name, `参数 ${index + 1}`)}
                schema={asRecord(parameter.schema)}
                schemas={schemas}
              />
            </div>
          ))
        ) : (
          <Text type="secondary">无请求参数</Text>
        )}
      </section>
      <BodySchema
        label="请求体"
        reference={textValue(endpoint.request_schema_ref)}
        contract={contract}
      />
      <BodySchema
        label="响应体"
        reference={textValue(endpoint.response_schema_ref)}
        contract={contract}
      />
    </div>
  )
}
