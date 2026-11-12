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
      quantity: {
        type: 'integer',
        minimum: 1,
        description: '采购数量',
        entity_field_ref: 'order.quantity'
      },
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
          {
            name: 'id',
            in: 'path',
            required: true,
            description: '路径编号说明',
            schema: { type: 'string' }
          },
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
  assert.ok(
    html.includes('采购数量') && html.includes('路径编号说明'),
    '展示 Schema 字段和参数中文说明'
  )
  assert.ok(!html.includes('order.quantity'), '中文说明替代字段实体关联标签')
  assert.ok(!html.includes('数组元素'), '纯引用数组直接展示命名元素类型，不重复套元素卡片')
  assert.ok(!html.includes('枚举值'), '枚举取值紧跟 enum，不另起一行')
  assert.ok(!html.includes('最小值'), '类型审阅不展示最小值约束')
  assert.ok(html.includes('请求参数如何发送') && html.includes('请求体如何发送'))
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
  assert.ok(!empty.includes('aria-label="请求参数"') && !empty.includes('aria-label="请求体"'))
  assert.ok(empty.includes('OrderResponse'))
  const bodyOnly = renderToStaticMarkup(
    <EndpointInspector
      contract={{ schemas }}
      endpoint={{ request_schema_ref: 'CreateOrderRequest' }}
    />
  )
  assert.ok(bodyOnly.includes('请求体如何发送') && !bodyOnly.includes('请求参数如何发送'))
  const missingBody = renderToStaticMarkup(
    <EndpointInspector contract={{ schemas }} endpoint={{ request_schema_ref: 'MissingRequest' }} />
  )
  assert.ok(missingBody.includes('请求体如何发送') && missingBody.includes('类型定义暂不可读取'))
})

test('嵌套请求参数沿用同一类型树与默认值列，保留零值、布尔、空字符串及空值', () => {
  const html = renderToStaticMarkup(
    <EndpointInspector
      contract={{ schemas }}
      endpoint={{
        parameters: [
          {
            name: 'filter',
            in: 'query',
            schema: {
              type: 'object',
              properties: {
                options: {
                  type: 'object',
                  properties: { enabled: { type: 'boolean', default: false } }
                },
                page: { type: 'integer', default: 0 },
                label: { type: 'string', default: '' },
                nullable: { type: ['string', 'null'], default: null },
                lines: { type: 'array', items: { $ref: 'OrderLine' } }
              }
            }
          }
        ]
      }}
    />
  )
  const defaults = Array.from(
    html.matchAll(
      /class="[^"]*technical-plan-schema-default" aria-label="默认值">([^<]*)<\/span>/g
    ),
    (match) => match[1]
  )
  assert.deepEqual(defaults, ['—', '—', 'false', '0', '&quot;&quot;', 'null', '—', '—', '—'])
  for (const text of ['filter', 'options', 'enabled', 'OrderLine', 'quantity', '循环引用']) {
    assert.ok(html.includes(text), `嵌套参数缺少：${text}`)
  }
  assert.ok(!html.includes('默认值：'), '各层默认值仅在默认值列展示一次')
  assert.ok(html.includes('technical-plan-schema-column-head'), '参数沿用正文的列标题')
})

test('数组元素简化层级仍保留额外约束、缺失引用和循环引用提示', () => {
  const constrained = renderToStaticMarkup(
    <SchemaTree
      name="List"
      schema={{ type: 'array', items: { $ref: 'OrderLine', maxProperties: 2 } }}
      schemas={schemas}
    />
  )
  assert.ok(constrained.includes('最多属性') && constrained.includes('quantity'))
  const missing = renderToStaticMarkup(
    <SchemaTree name="List" schema={{ type: 'array', items: { $ref: 'Missing' } }} />
  )
  assert.ok(missing.includes('引用类型暂不可读取：Missing'))
  const circular = renderToStaticMarkup(
    <SchemaTree name="List" schema={{ type: 'array', items: { $ref: 'List' } }} />
  )
  assert.ok(circular.includes('循环引用：List'))
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
