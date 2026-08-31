import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  applyEntityDesignSuggestion,
  constraintRowsToFieldValues,
  defaultConstraintRows,
  externalApiCollectionValidationErrors,
  externalApiConnectionValidationErrors,
  externalApiValidationErrors,
  fieldValuesToConstraintRows,
  formatJsonText,
  isSensitiveApiHeader,
  normalizeFieldValues,
  normalizeObjectRows,
  normalizeStringList,
  parseJsonImport,
  parseJsonList,
  parseJsonRecord,
  resolveEntityDesignFields,
  responseFieldPaths,
  responseFieldTypes,
  sameNameFieldMappings,
  serializeExternalApiDesign,
  serializeExternalApiMappings,
  seedRowsFromSuggestions,
  serializeSeedRows,
  tryParseJson
} from '../src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/entityDesignSerialization'
import { fetchDatabaseTableColumns } from '../src/renderer/src/service/workspaceTools'
import {
  createExternalApiOperation,
  externalApiDesignDraftSeed
} from '../src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/useExternalApiDraft'

test('fetchDatabaseTableColumns 合并同键并发请求并在结束后清理', async () => {
  const globalWithBrowser = globalThis as typeof globalThis & {
    window?: { xcodeAgent?: { agentBaseUrl?: string } }
  }
  const previousWindow = globalWithBrowser.window
  const previousFetch = globalThis.fetch
  let requestCount = 0
  let releaseRequest: (() => void) | undefined
  const requestReleased = new Promise<void>((resolve) => {
    releaseRequest = resolve
  })

  globalWithBrowser.window = { xcodeAgent: { agentBaseUrl: 'http://agent.test' } }
  globalThis.fetch = (async () => {
    requestCount += 1
    await requestReleased
    return {
      ok: true,
      json: async () => ({
        tool: 'database.table_columns',
        status: 'ok',
        table_name: 'category',
        columns: []
      })
    } as Response
  }) as typeof fetch

  try {
    const first = fetchDatabaseTableColumns({
      workspace_root: 'C:/workspace/',
      table_name: 'category'
    })
    const second = fetchDatabaseTableColumns({
      workspace_root: 'C:/workspace',
      table_name: ' category '
    })
    assert.equal(requestCount, 1)
    releaseRequest?.()
    const [firstResult, secondResult] = await Promise.all([first, second])
    assert.deepEqual(firstResult, secondResult)

    await fetchDatabaseTableColumns({
      workspace_root: 'C:/workspace',
      table_name: 'category'
    })
    assert.equal(requestCount, 2)

    const differentTable = fetchDatabaseTableColumns({
      workspace_root: 'C:/workspace',
      table_name: 'product'
    })
    assert.equal(requestCount, 3)
    await differentTable
  } finally {
    globalThis.fetch = previousFetch
    if (previousWindow) {
      globalWithBrowser.window = previousWindow
    } else {
      delete globalWithBrowser.window
    }
  }
})

test('normalizeObjectRows 丢弃空白行并与旧 JSON 载荷等价', () => {
  const rows = [
    { target_field: 'product_name', source: 'product.name', rule: '' },
    { target_field: '  ', source: '', rule: '' },
    { target_field: 'price', source: 'product.price', rule: 'decimal' }
  ]
  const normalized = normalizeObjectRows(rows)
  assert.equal(normalized.length, 2)
  // 序列化后经旧 JSON 解析仍得到等价的行对象。
  const reparsed = parseJsonList(JSON.stringify(normalized))
  assert.deepEqual(reparsed, normalized)
})

test('serializeSeedRows 只保留非空字段并转字符串', () => {
  const rows = [
    { name: '商品A', price: 12.5, _empty: '' },
    { name: '', price: '' }
  ]
  const serialized = serializeSeedRows(rows)
  assert.equal(serialized.length, 1)
  assert.deepEqual(serialized[0], { name: '商品A', price: '12.5' })
})

test('normalizeStringList 去空格、去重并忽略空值', () => {
  assert.deepEqual(normalizeStringList([' 完成 ', '', '完成', ' 通过 ']), ['完成', '通过'])
  assert.deepEqual(normalizeStringList(undefined), [])
})

test('normalizeFieldValues 丢弃空数组与空键', () => {
  const normalized = normalizeFieldValues({
    status: ['draft', 'approved'],
    empty: [],
    '  ': ['x']
  })
  assert.deepEqual(normalized, { status: ['draft', 'approved'] })
})

test('parseJsonRecord 兼容字符串数组并把非数组值归一为空数组', () => {
  const parsed = parseJsonRecord('{"status":["draft"],"note":"not-array"}')
  assert.deepEqual(parsed, { status: ['draft'], note: [] })
})

test('tryParseJson 空文本返回 undefined，非法 JSON 保留原文本', () => {
  assert.equal(tryParseJson('  '), undefined)
  assert.equal(tryParseJson('not json'), 'not json')
  assert.deepEqual(tryParseJson('{"a":1}'), { a: 1 })
})

test('parseJsonImport 解析合法 JSON 并返回错误结果给非法文本', () => {
  assert.deepEqual(parseJsonImport('  {"a": 1}  '), { ok: true, value: { a: 1 } })
  const empty = parseJsonImport('   ')
  assert.equal(empty.ok, false)
  const invalid = parseJsonImport('{bad')
  assert.equal(invalid.ok, false)
  assert.equal(String(invalid.error).length > 0, true)
})

test('responseFieldPaths 收集顶层与嵌套路径，数组取首元素', () => {
  const paths = responseFieldPaths({
    data: { items: [{ name: '商品A', price: 1 }] },
    page: 1
  })
  assert.deepEqual(paths, ['data', 'data.items', 'data.items[]', 'data.items[].name', 'data.items[].price', 'page'])
  assert.deepEqual(responseFieldPaths([{ name: '商品A' }]), ['[]', '[].name'])
  assert.deepEqual(responseFieldTypes({ data: { items: [{ name: '商品A' }] } }), {
    data: 'object',
    'data.items': 'array',
    'data.items[]': 'array',
    'data.items[].name': 'string'
  })
  assert.deepEqual(responseFieldPaths('not-json'), [])
  assert.deepEqual(responseFieldPaths(null), [])
})

test('formatJsonText 与 externalApiValidationErrors 校验可执行契约', () => {
  assert.deepEqual(formatJsonText('{"data": {"name": "A"}}'), {
    ok: true,
    text: '{\n  "data": {\n    "name": "A"\n  }\n}'
  })
  const errors = externalApiValidationErrors({
    baseUrl: 'https://api.example.com',
    baseUrlConfigKey: 'integrations.product.base-url',
    method: 'GET',
    path: '/products/{id}',
    parameters: [{ name: 'id', in: 'path', type: 'string', required: true }],
    headers: [],
    requestBody: '',
    responseBody: { data: { items: [{ name: 'A' }], total: 1 } },
    responseHandling: {
      cardinality: 'page',
      payload_path: 'data.items[]',
      total_path: 'data.total',
      pagination: { page_parameter: 'page', size_parameter: 'size' }
    },
    mappings: [{ entity_field: 'name', source_field: 'data.items[].name' }],
    entityFields: [{ name: 'name', required: true }]
  })
  assert.ok(errors.some((error) => error.includes('分页页码参数')))
})

test('sameNameFieldMappings 顶层同名优先、嵌套唯一路径兜底且不覆盖已填映射', () => {
  const entityFields = [
    { name: 'name', label: '名称' },
    { name: 'price', label: '价格' },
    { name: 'status', label: '状态' },
    { name: 'description', label: '说明' }
  ]
  const current = [
    { entity_field: 'name', source_field: 'data.title', rule: 'manual' },
    { entity_field: 'price', source_field: '', rule: '' }
  ]
  const rows = sameNameFieldMappings(
    entityFields,
    { data: { items: [{ price: 1, status: 'ok' }] }, name: 'x' },
    current
  )
  const byField = new Map(rows.map((row) => [String(row.entity_field), row]))
  // 顶层同名优先
  assert.equal(byField.get('name')?.source_field, 'data.title')
  assert.equal(byField.get('name')?.rule, 'manual')
  // 嵌套唯一叶子路径
  assert.equal(byField.get('price')?.source_field, 'data.items[].price')
  assert.equal(byField.get('price')?.rule, 'nested_match')
  // 嵌套叶子路径同样适配其他字段
  assert.equal(byField.get('status')?.source_field, 'data.items[].status')
  // 返回体中不存在的选填字段不生成空映射行
  assert.equal(byField.has('description'), false)
})

test('外部 API 校验拒绝 GET 残留请求体、失效错误路径与空 Header 名称', () => {
  const errors = externalApiValidationErrors({
    baseUrl: 'https://api.example.com',
    baseUrlConfigKey: 'integrations.product.base-url',
    method: 'GET',
    path: '/products',
    parameters: [],
    headers: [{ name: '', value: 'application/json' }],
    requestBody: '{"name":"A"}',
    responseBody: { data: { name: 'A' } },
    responseHandling: { cardinality: 'object', payload_path: 'data', error_message_path: 'error.message' },
    mappings: [{ entity_field: 'name', source_field: 'data.name' }],
    entityFields: [{ name: 'name', required: true }]
  })
  assert.ok(errors.some((error) => error.includes('GET 请求')))
  assert.ok(errors.some((error) => error.includes('错误信息路径不存在')))
  assert.ok(errors.some((error) => error.includes('Header')))
})

test('serializeExternalApiMappings 丢弃选填字段空映射并规范化规则', () => {
  assert.deepEqual(serializeExternalApiMappings([
    { entity_field: 'name', source_field: 'data.name', rule: 'same_name' },
    { entity_field: 'description', source_field: '', rule: '' },
    { entity_field: 'status', source_field: 'data.status', rule: 'unknown' }
  ]), [
    { entity_field: 'name', source_field: 'data.name', rule: 'same_name' },
    { entity_field: 'status', source_field: 'data.status', rule: 'manual' }
  ])
})

test('isSensitiveApiHeader 识别 API Key 与 Token 名称变体', () => {
  assert.equal(isSensitiveApiHeader('X_API_KEY'), true)
  assert.equal(isSensitiveApiHeader('client-access-token'), true)
  assert.equal(isSensitiveApiHeader('Accept-Language'), false)
})

test('外部 API 当前契约不支持 PATCH 请求方式', () => {
  const errors = externalApiValidationErrors({
    baseUrl: 'https://api.example.com',
    baseUrlConfigKey: 'integrations.product.base-url',
    method: 'PATCH',
    path: '/products',
    parameters: [],
    headers: [],
    requestBody: '{"name":"A"}',
    responseBody: { name: 'A' },
    responseHandling: { cardinality: 'object', payload_path: '' },
    mappings: [{ entity_field: 'name', source_field: 'name' }],
    entityFields: [{ name: 'name', required: true }]
  })
  assert.ok(errors.some((error) => error.includes('请求方式必须是 GET、POST、PUT 或 DELETE')))
})

test('外部 API 多操作契约校验 Endpoint 唯一分配且允许分批覆盖', () => {
  const relatedEndpoints = [
    { api_contract_id: 'products-api', endpoint_id: 'products.list' },
    { api_contract_id: 'products-api', endpoint_id: 'products.detail' }
  ]
  const errors = externalApiCollectionValidationErrors({
    relatedEndpoints,
    operations: [
      {
        operationId: 'products-query',
        name: '查询商品',
        endpointRefs: [relatedEndpoints[0], relatedEndpoints[0]]
      },
      {
        operationId: 'products-copy',
        name: '重复查询',
        endpointRefs: [relatedEndpoints[0]]
      }
    ]
  })
  assert.ok(errors.some((error) => error.includes('重复关联同一 Endpoint')))
  assert.ok(errors.some((error) => error.includes('不能同时绑定操作')))
  assert.ok(!errors.some((error) => error.includes('products.detail')))
})

test('外部 API 连接覆盖要求 Base URL 与配置键成对填写', () => {
  assert.ok(externalApiConnectionValidationErrors({
    baseUrl: 'https://other.example.com',
    baseUrlConfigKey: '',
    required: false
  }).some((error) => error.includes('同时提供配置键')))
})

test('非实体响应无需字段映射但拒绝分页语义', () => {
  const errors = externalApiValidationErrors({
    baseUrl: 'https://api.example.com',
    baseUrlConfigKey: 'integrations.product.base-url',
    method: 'DELETE',
    path: '/products/{id}',
    parameters: [{ name: 'id', in: 'path', type: 'string', required: true }],
    headers: [],
    requestBody: '',
    responseBody: { success: true },
    responseHandling: { entity_payload: false, cardinality: 'page', payload_path: 'data' },
    mappings: [],
    entityFields: [{ name: 'name', required: true }],
    entityPayload: false
  })
  assert.ok(errors.some((error) => error.includes('非实体响应不得配置')))
  assert.ok(!errors.some((error) => error.includes('必填字段尚未映射')))
})

test('多操作草稿最终序列化为当前 connection + operations 契约', () => {
  const design = serializeExternalApiDesign({
    connection: {
      baseUrl: ' https://api.example.com ',
      baseUrlConfigKey: ' integrations.product.base-url ',
      timeoutMs: 10000,
      headers: [{ name: ' X-Locale ', value: 'zh-CN' }, { name: '', value: '' }]
    },
    activeOperationId: 'products-delete',
    operations: [{
      operationId: 'products-delete',
      name: ' 删除商品 ',
      endpointRefs: [{ api_contract_id: 'products-api', endpoint_id: 'products.delete' }],
      overrideBaseUrl: '',
      overrideBaseUrlConfigKey: '',
      overrideTimeoutMs: undefined,
      method: 'DELETE',
      path: ' /v1/products/{id} ',
      parameters: [{ name: 'id', in: 'path', type: 'string', required: true }],
      headers: [],
      requestBody: '',
      responseBody: '{"success":true}',
      responseHandling: {
        entity_payload: false,
        cardinality: 'page',
        payload_path: 'data',
        success_status_codes: ['204'],
        pagination: { page_parameter: 'page', size_parameter: 'size' },
        total_path: 'total'
      },
      mappings: [{ entity_field: 'id', source_field: 'data.id', rule: 'manual' }]
    }]
  })

  assert.equal(design.connection.base_url, 'https://api.example.com')
  assert.deepEqual(design.connection.headers, [{ name: 'X-Locale', value: 'zh-CN' }])
  assert.equal(design.operations[0].response_handling.entity_payload, false)
  assert.equal(design.operations[0].response_handling.cardinality, 'object')
  assert.equal(design.operations[0].response_handling.payload_path, '')
  assert.equal(design.operations[0].response_handling.success_status_codes[0], 204)
  assert.equal(design.operations[0].response_handling.pagination, undefined)
  assert.deepEqual(design.operations[0].field_mappings, [])
})

test('外部 API 草稿只恢复当前多操作契约并生成独立操作 ID', () => {
  const draft = externalApiDesignDraftSeed({
    connection: {
      base_url: 'https://api.example.com',
      base_url_config_key: 'integrations.product.base-url',
      timeout_ms: 5000,
      headers: []
    },
    operations: [{
      operation_id: 'products-list',
      name: '查询商品',
      endpoint_refs: [{ api_contract_id: 'products-api', endpoint_id: 'products.list' }],
      api_info: {
        method: 'GET',
        path: '/products',
        parameters: [],
        headers: [],
        response_body: { items: [] }
      },
      response_handling: { entity_payload: true, cardinality: 'array', payload_path: 'items' },
      field_mappings: []
    }]
  })
  const removedShape = externalApiDesignDraftSeed({
    api_info: { method: 'GET', path: '/removed' }
  })

  assert.equal(draft.activeOperationId, 'products-list')
  assert.equal(draft.operations[0].path, '/products')
  assert.equal(removedShape.operations.length, 0)
  assert.notEqual(createExternalApiOperation('copy-id').operationId, draft.operations[0].operationId)
})

test('seedRowsFromSuggestions 提取有效种子记录并忽略非法项', () => {
  const rows = seedRowsFromSuggestions([
    {
      id: 'seed_data-0',
      label: '种子记录 1',
      payload: { seed_row: { name: '商品A', price: '12.5' } }
    },
    { id: 'seed_data-1', label: '种子记录 2', payload: {} },
    { id: 'seed_data-2', label: '种子记录 3', payload: { seed_row: {} } },
    {
      id: 'seed_data-3',
      label: '种子记录 4',
      payload: { seed_row: { name: '商品B' } }
    },
    'not-a-suggestion',
    null
  ])
  assert.deepEqual(rows, [
    { name: '商品A', price: '12.5' },
    { name: '商品B' }
  ])
})

test('seedRowsFromSuggestions 空结果返回空数组', () => {
  assert.deepEqual(seedRowsFromSuggestions([]), [])
  assert.deepEqual(seedRowsFromSuggestions(undefined), [])
})

test('fieldValuesToConstraintRows 转约束行并丢弃空字段/空取值', () => {
  const rows = fieldValuesToConstraintRows({
    status: ['draft', 'approved'],
    empty: [],
    '  ': ['x'],
    note: ['a', 'a']
  })
  assert.deepEqual(rows, [
    { field: 'status', values: ['draft', 'approved'] },
    { field: 'note', values: ['a'] }
  ])
})

test('constraintRowsToFieldValues 转回记录并丢弃空行', () => {
  const record = constraintRowsToFieldValues([
    { field: 'status', values: ['draft', 'approved'] },
    { field: '', values: ['x'] },
    { field: 'note', values: [] },
    { field: '  ', values: ['y'] },
    'not-a-row'
  ])
  assert.deepEqual(record, { status: ['draft', 'approved'] })
})

test('resolveEntityDesignFields 优先非空 target.fields，空数组回退 design.fields', () => {
  const target = {
    fields: [{ name: 'status', type: 'enum', enum_values: ['on', 'off'] }]
  }
  const design = { fields: [{ name: 'name', type: 'text' }] }
  assert.deepEqual(resolveEntityDesignFields(target, design), target.fields)
  assert.deepEqual(resolveEntityDesignFields({ fields: [] }, design), design.fields)
  assert.deepEqual(resolveEntityDesignFields(undefined, design), design.fields)
  assert.deepEqual(resolveEntityDesignFields(undefined, undefined), [])
})

test('字段取值约束行与记录互转往返一致', () => {
  const original = { status: ['draft'], price: ['1', '2'] }
  const roundtrip = constraintRowsToFieldValues(
    fieldValuesToConstraintRows(original)
  )
  assert.deepEqual(roundtrip, original)
})

test('defaultConstraintRows 与 constraintRowsToFieldValues 合并时保留已有行', () => {
  const entityFields = [
    { name: 'status', label: '状态', type: 'enum', enum_values: ['on', 'off'] },
    { name: 'mode', label: '模式', type: 'enum', enum_values: ['a', 'b'] }
  ]
  const current = [{ field: 'status', values: ['on'] }]
  const merged = defaultConstraintRows(
    entityFields,
    constraintRowsToFieldValues(current)
  )
  assert.deepEqual(merged, [
    { field: 'status', values: ['on'] },
    { field: 'mode', values: ['a', 'b'] }
  ])
})

test('defaultConstraintRows 仅对 enum 字段生成默认行并与已有约束去重', () => {
  const entityFields = [
    { name: 'status', label: '状态', type: 'enum', enum_values: ['on', 'off'] },
    { name: 'price', label: '价格', type: 'number' },
    { name: 'mode', label: '模式', type: 'enum', enum_values: ['a', 'b'] },
    { name: 'empty_enum', label: '空枚举', type: 'enum', enum_values: [] }
  ]
  const rows = defaultConstraintRows(entityFields, { status: ['draft'] })
  assert.deepEqual(rows, [
    { field: 'status', values: ['draft'] },
    { field: 'mode', values: ['a', 'b'] }
  ])
})

test('defaultConstraintRows 无已有约束的 enum 字段按实体顺序追加', () => {
  const entityFields = [
    { name: 'mode', label: '模式', type: 'enum', enum_values: ['a', 'b'] },
    { name: 'status', label: '状态', type: 'enum', enum_values: ['on', 'off'] }
  ]
  const rows = defaultConstraintRows(entityFields, {})
  assert.deepEqual(rows, [
    { field: 'mode', values: ['a', 'b'] },
    { field: 'status', values: ['on', 'off'] }
  ])
})

test('defaultConstraintRows type 缺失但 enum_values 非空时仍生成默认行', () => {
  const entityFields = [
    { name: 'status', label: '状态', enum_values: ['on', 'off'] },
    { name: 'mode', label: '模式', type: 'text', enum_values: ['a'] }
  ]
  const rows = defaultConstraintRows(entityFields, {})
  assert.deepEqual(rows, [{ field: 'status', values: ['on', 'off'] }])
})

test('applyEntityDesignSuggestion 绑定建议合并到已有行或追加新行', () => {
  const current = [
    { entity_field: 'name', table_column: '', rule: '' },
    { entity_field: 'price', table_column: '', rule: '' }
  ]
  const next = applyEntityDesignSuggestion('bindings', current, {
    id: 'bindings-0',
    label: 'price → product_price',
    payload: { entity_field: 'price', table_column: 'product_price', rule: 'same_name' }
  }) as Array<Record<string, unknown>>
  assert.equal(next.length, 2)
  assert.equal(next[1].table_column, 'product_price')
  const added = applyEntityDesignSuggestion('bindings', current, {
    id: 'bindings-1',
    label: 'status → status',
    payload: { entity_field: 'status', table_column: 'status' }
  }) as Array<Record<string, unknown>>
  assert.equal(added.length, 3)
  assert.equal(added[2].entity_field, 'status')
})

test('applyEntityDesignSuggestion 规则/关系追加对象，验收/风险追加文本', () => {
  const rules = applyEntityDesignSuggestion('business_rules', [], {
    id: 'rules-0',
    label: '编码唯一',
    payload: { rule_type: 'unique', name: '编码唯一', description: '商品编码唯一' }
  }) as Array<Record<string, unknown>>
  assert.equal(rules.length, 1)
  assert.equal(rules[0].name, '编码唯一')

  const acceptance = applyEntityDesignSuggestion('acceptance', ['列表可查询'], {
    id: 'acceptance-0',
    label: '详情可打开',
    value: '详情可打开'
  }) as string[]
  assert.deepEqual(acceptance, ['列表可查询', '详情可打开'])
})

test('applyEntityDesignSuggestion api_mapping 映射按实体字段合并或追加', () => {
  const current = [
    { entity_field: 'name', source_field: '', rule: '' },
    { entity_field: 'price', source_field: '', rule: '' }
  ]
  const next = applyEntityDesignSuggestion('api_mapping', current, {
    id: 'api_mapping-0',
    label: 'price ← data.price',
    payload: { entity_field: 'price', source_field: 'data.price', rule: 'same_name' }
  }) as Array<Record<string, unknown>>
  assert.equal(next.length, 2)
  assert.equal(next[1].source_field, 'data.price')
  const added = applyEntityDesignSuggestion('api_mapping', current, {
    id: 'api_mapping-1',
    label: 'status ← status',
    payload: { entity_field: 'status', source_field: 'status' }
  }) as Array<Record<string, unknown>>
  assert.equal(added.length, 3)
  assert.equal(added[2].source_field, 'status')
})

test('applyEntityDesignSuggestion seed_data 追加种子记录', () => {
  const next = applyEntityDesignSuggestion('seed_data', [], {
    id: 'seed_data-0',
    label: '种子记录 1',
    payload: { seed_row: { name: '商品A', price: '12.5' } }
  }) as Array<Record<string, unknown>>
  assert.deepEqual(next, [{ name: '商品A', price: '12.5' }])
})
