import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { WorkflowApiDesignDraft, WorkflowApiField } from '../src/renderer/src/typings/workflow'
import { allowedFilterOperators, defaultDatabaseOperation, defaultDatabaseUsageForOperation, validateApiDesignDraft } from '../src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/apiDesignSerialization'
import { inferSelection, resetDraftForDatabaseOperation } from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/model'

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
  assert.match(validateApiDesignDraft({ ...draft, databaseQuery: { join: 'and', items: [{ ...valueCondition, right: { kind: 'fixed', value: [10, 1] } }] } }).__databaseQuery || '', /有效运算符和右值/)
})
