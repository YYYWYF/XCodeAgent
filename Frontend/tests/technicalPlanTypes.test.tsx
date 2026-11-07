import assert from 'node:assert/strict'
import { test } from 'node:test'
import { renderToStaticMarkup } from 'react-dom/server'
import { EndpointInspector } from '../src/renderer/src/components/AiChatPanel/components/DocPanel/TechnicalPlanEndpointInspector'
import {
  SchemaTree,
  schemaDependencyNames
} from '../src/renderer/src/components/AiChatPanel/components/DocPanel/TechnicalPlanSchemaTree'
import RevisionDraftReview from '../src/renderer/src/components/AiChatPanel/components/ApplicationRevisionCard/RevisionDraftReview'

const schemas = {
  CreateOrderRequest: {
    type: 'object',
    required: ['lines'],
    properties: {
      lines: { type: 'array', items: { $ref: 'OrderLine' } },
      shipping: { type: 'object', properties: { address: { type: 'string', minLength: 1 } } }
    }
  },
  OrderLine: {
    type: 'object',
    required: ['quantity'],
    properties: {
      quantity: { type: 'integer', minimum: 1, description: '采购数量' },
      next: { $ref: 'OrderLine' }
    }
  },
  OrderResponse: {
    allOf: [
      { type: 'object', properties: { id: { type: 'string' } } },
      {
        type: 'object',
        properties: { state: { type: ['string', 'null'], enum: ['待确认', null] } }
      }
    ]
  }
}

test('请求参数不截断，请求体和响应体同时展示完整字段及引用类型', () => {
  const html = renderToStaticMarkup(
    <EndpointInspector
      contract={{ schemas }}
      endpoint={{
        method: 'POST',
        path: '/api/orders/{id}',
        name: '创建订单',
        parameters: [
          { name: 'id', in: 'path', required: true, description: '路径编号说明', schema: { type: 'string' } },
          {
            name: 'filter',
            in: 'query',
            schema: { type: 'object', properties: { keyword: { type: 'string' } } }
          },
          { name: 'X-Request-Id', in: 'header', schema: { type: 'string', format: 'uuid' } },
          {
            name: 'fourthParameter',
            in: 'query',
            schema: { type: 'array', items: { $ref: 'OrderLine' } }
          }
        ],
        request_schema_ref: 'CreateOrderRequest',
        response_schema_ref: 'OrderResponse'
      }}
    />
  )
  for (const text of [
    '请求参数',
    '请求体',
    '响应体',
    'path',
    'query',
    'header',
    'X-Request-Id',
    'fourthParameter',
    'keyword',
    'CreateOrderRequest',
    'OrderResponse',
    'OrderLine',
    'lines',
    'shipping',
    'address',
    'quantity',
    'integer',
    '最小值',
    '最短长度',
    '格式',
    'uuid',
    'allOf',
    'state',
    'enum',
    'API 契约类型（JSON Schema），数据库列类型单独定义',
    '待确认',
    '循环引用'
  ]) {
    assert.ok(html.includes(text), `完整类型展示缺少：${text}`)
  }
  assert.deepEqual(schemaDependencyNames(schemas.CreateOrderRequest, schemas), ['OrderLine'])
  assert.ok(!html.includes('采购数量') && !html.includes('路径编号说明'), '不展示字段中文说明')
  assert.ok(!html.includes('枚举值'), '枚举取值紧跟 enum，不另起一行')
})

test('根数组和多层数组保留元素定义，缺少请求体的接口正常显示响应', () => {
  const html = renderToStaticMarkup(
    <SchemaTree
      name="Batch"
      schema={{
        type: 'array',
        items: { type: 'array', items: { $ref: 'OrderLine' } }
      }}
      schemas={schemas}
    />
  )
  for (const text of ['OrderLine[][]', '数组元素', 'quantity', 'integer', '循环引用']) {
    assert.ok(html.includes(text), `根数组缺少：${text}`)
  }
  const empty = renderToStaticMarkup(
    <EndpointInspector
      contract={{ schemas }}
      endpoint={{
        response_schema_ref: 'OrderResponse'
      }}
    />
  )
  assert.ok(empty.includes('无请求参数') && empty.includes('无请求体'))
  assert.ok(empty.includes('OrderResponse'))
})

test('正式修订以结构化草稿审阅，保留确认修改放弃且不展示 Markdown 编辑器', () => {
  const html = renderToStaticMarkup(
    <RevisionDraftReview
      productPlan={{}}
      technicalPlan={{
        api_contracts: [
          {
            id: 'orders',
            schemas,
            endpoints: [
              {
                id: 'create',
                name: '创建订单',
                request_schema_ref: 'CreateOrderRequest',
                response_schema_ref: 'OrderResponse'
              }
            ]
          }
        ]
      }}
      interaction={{
        changeId: 'change',
        interactionId: 'gate',
        basedOnLifecycleRevision: 1,
        artifactKey: 'technical-plan',
        draftSha256: 'a'.repeat(64)
      }}
      onAction={() => undefined}
    />
  )
  for (const text of [
    'CreateOrderRequest',
    'OrderResponse',
    '提出修改',
    '确认当前版本',
    '放弃本次修改'
  ]) {
    assert.ok(html.includes(text), `修订草稿缺少：${text}`)
  }
  assert.ok(!html.includes('Markdown') && !html.includes('保存草稿'))
})
