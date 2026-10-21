import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { WorkflowApiDesignDraft, WorkflowApiField } from '../src/renderer/src/typings/workflow'
import { allowedFilterOperators, defaultDatabaseOperation, defaultDatabaseUsageForOperation, validateApiDesignDraft } from '../src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/apiDesignSerialization'
import { inferSelection, resetDraftForDatabaseOperation } from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/model'
import { valueSummary } from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/valueRules'

/** 构造用于验证 CRUD 切换的请求字段。 */
function field(location: WorkflowApiField['location'], path: string): WorkflowApiField {
  return { nodeType: 'endpoint_field', id: `${location}:${path}`, side: 'request', location, path, type: 'string', required: true, description: '' }
}

/** 验证 HTTP 方法到单一数据库 CRUD 操作的默认推导。 */
test('database CRUD defaults follow HTTP method', () => {
  assert.equal(defaultDatabaseOperation('POST'), 'create')
  assert.equal(defaultDatabaseOperation('GET'), 'read')
  assert.equal(defaultDatabaseOperation('PATCH'), 'update')
  assert.equal(defaultDatabaseOperation('OPTIONS'), undefined)
})

/** 验证操作切换保留写入列并在新增时清空查询条件。 */
test('changing CRUD operation clears query when creating', () => {
  const pathField = field('path', 'id')
  const bodyField = field('request_body', 'name')
  const draft: WorkflowApiDesignDraft = {
    apiContractId: 'orders', endpointId: 'update', databaseOperation: 'update',
    databaseQuery: { join: 'and', items: [{ kind: 'condition', sourceType: 'database', sourceId: 'db', schema: 'app', table: 'orders', column: 'id', type: 'string', operator: 'eq', right: { kind: 'endpoint', endpointField: pathField } }] },
    databaseWrites: [{ sourceType: 'database', sourceId: 'db', schema: 'app', table: 'orders', column: 'name', type: 'string', right: { kind: 'endpoint', endpointField: { side: 'request', location: 'request_body', path: 'name', type: 'string', required: true, description: '' } } }],
    fieldMappings: [{ endpointField: bodyField, mappingType: 'business_description', businessDescription: '写入订单名称' }]
  }
  const next = resetDraftForDatabaseOperation(draft, 'create')
  assert.equal(next.databaseWrites?.[0].column, 'name')
  assert.equal(next.databaseQuery, undefined)
  assert.equal(defaultDatabaseUsageForOperation(pathField, 'delete'), 'write')
  assert.deepEqual(validateApiDesignDraft(next), {})
})

/** 验证类型感知运算符及允许重复列的条件树。 */
test('database query tree keeps repeated columns', () => {
  assert.ok(allowedFilterOperators('string', 'varchar(100)').includes('contains'))
  assert.ok(allowedFilterOperators('integer', 'bigint').includes('gte'))
  assert.ok(allowedFilterOperators('array<number>', 'decimal').includes('between'))
  assert.ok(!allowedFilterOperators('string', 'varchar(100)').includes('between'))
  const response = field('response_body', 'id')
  response.side = 'response'
  const condition = { kind: 'condition' as const, sourceType: 'database' as const, sourceId: 'db', schema: 'app', table: 'orders', column: 'deleted_at', type: 'datetime', operator: 'is_null' as const }
  const draft: WorkflowApiDesignDraft = {
    apiContractId: 'orders', endpointId: 'list', databaseOperation: 'read', databaseQuery: { join: 'and', items: [condition, { kind: 'group', join: 'or', items: [{ ...condition }] }] },
    fieldMappings: [{ endpointField: response, mappingType: 'source_mapping', processingType: 'direct', sourceFields: [{ sourceType: 'database', sourceId: 'db', schema: 'app', table: 'orders', column: 'id', type: 'integer', usage: 'read' }] }]
  }
  assert.equal(validateApiDesignDraft(draft).__databaseQuery, undefined)
  assert.equal(inferSelection(draft).selection?.sourceType, 'database')

  const valueCondition = { ...condition, column: 'amount', type: 'decimal', operator: 'between' as const, right: { kind: 'fixed' as const, value: [1, 10] } }
  assert.equal(validateApiDesignDraft({ ...draft, databaseQuery: { join: 'and', items: [valueCondition] } }).__databaseQuery, undefined)
  assert.match(validateApiDesignDraft({ ...draft, databaseQuery: { join: 'and', items: [{ ...valueCondition, right: { kind: 'fixed', value: [10, 1] } }] } }).__databaseQuery || '', /固定值格式或类型不正确/)
})

/** 同一入参可供多个外部目标加工，目标重复和失效依赖仍阻止确认。 */
test('external target rules reuse inputs and retain missing policy', () => {
  const request = field('query', 'page')
  const right = { kind: 'business' as const, origin: 'endpoint' as const, endpointFields: [request], builtinFields: [], businessDescription: '将页码转成偏移量。', missingBehavior: 'default' as const, defaultValue: 0 }
  const draft: WorkflowApiDesignDraft = { apiContractId: 'products', endpointId: 'list', fieldMappings: [{ endpointField: request, mappingType: 'unconfigured' }], externalApiBindings: ['offset', 'start'].map((path) => ({ externalField: { sourceType: 'external_api', sourceId: 'api', directoryId: 'dir', operationId: 'op', section: 'query', path, type: 'integer' }, right })) }
  assert.deepEqual(validateApiDesignDraft(draft), {})
  // 缺值策略仍保存在契约内，但当前界面摘要按产品规则隐藏它。
  assert.equal(right.missingBehavior, 'default')
  assert.equal(right.defaultValue, 0)
  assert.match(valueSummary(right), /输入：page/)
  assert.doesNotMatch(valueSummary(right), /默认值/)
  assert.equal(inferSelection(draft).complex, false)
  const duplicate = { ...draft, externalApiBindings: [draft.externalApiBindings![0], draft.externalApiBindings![0]] }
  assert.match(validateApiDesignDraft(duplicate)['__externalApiBinding:query:offset'], /重复配置/)
  assert.match(validateApiDesignDraft({ ...draft, fieldMappings: [] }).__valueRules, /失效/)
})

/** 无外部入参且返回值由业务生成时，显式来源允许请求字段不做物理映射。 */
test('external source binding validates without physical field mappings', () => {
  const request = field('query', 'traceId')
  const response = { ...field('response_body', 'accepted'), side: 'response' as const, type: 'boolean' }
  const draft: WorkflowApiDesignDraft = {
    apiContractId: 'products', endpointId: 'refresh',
    sourceBinding: { sourceType: 'external_api', sourceId: 'api', directoryId: 'dir', operationId: 'refresh' },
    externalApiBindings: [],
    fieldMappings: [
      { endpointField: request, mappingType: 'unconfigured' },
      { endpointField: response, mappingType: 'value_mapping', right: { kind: 'business', origin: 'business', endpointFields: [], builtinFields: [], businessDescription: '刷新调用成功后返回 true。', missingBehavior: 'error' } }
    ]
  }
  assert.deepEqual(validateApiDesignDraft(draft), {})
  assert.match(validateApiDesignDraft({ ...draft, sourceBinding: undefined })['request:query:traceId'], /请配置接口参数/)
  assert.match(validateApiDesignDraft({ ...draft, fieldMappings: [draft.fieldMappings[0], { endpointField: response, mappingType: 'unconfigured' }] })['response:response_body:accepted'], /请配置/)
})

/** 多字段业务加工仍属于同一表，全部业务输出也能恢复显式来源选择。 */
test('processed source fields stay in the single-source workspace', () => {
  const endpoint = { ...field('response_body', 'label'), side: 'response' as const }
  const sourceBinding = { sourceType: 'database' as const, sourceId: 'db', schema: 'app', table: 'products' }
  const draft: WorkflowApiDesignDraft = { apiContractId: 'products', endpointId: 'list', sourceBinding, databaseOperation: 'read', fieldMappings: [{ endpointField: endpoint, mappingType: 'source_mapping', processingType: 'multi_field_description', sourceFields: ['name', 'code'].map((column) => ({ ...sourceBinding, column, type: 'string', usage: 'read' })), businessDescription: '名称与编码拼接。' }] }
  assert.deepEqual(inferSelection(draft), { selection: sourceBinding, complex: false })
  assert.deepEqual(inferSelection({ ...draft, fieldMappings: [] }), { selection: sourceBinding, complex: false })
})
