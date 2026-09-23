import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { WorkflowApiDesignDraft, WorkflowApiField } from '../src/renderer/src/typings/workflow'
import { allowedFilterOperators, defaultDatabaseOperation, defaultDatabaseUsageForOperation, validateApiDesignDraft } from '../src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/apiDesignSerialization'
import { inferSelection, resetDraftForDatabaseOperation } from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/model'

/** 构造用于验证 CRUD 自动分区的请求字段。 */
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

/** 验证操作切换保留数据库列但重排查询/写入用途。 */
test('changing CRUD operation re-partitions request mappings', () => {
  const pathField = field('path', 'id')
  const bodyField = field('request_body', 'name')
  const draft: WorkflowApiDesignDraft = {
    apiContractId: 'orders', endpointId: 'update', databaseOperation: 'update', databaseConditions: [],
    fieldMappings: [
      { endpointField: pathField, mappingType: 'source_mapping', processingType: 'direct', sourceFields: [{ sourceType: 'database', sourceId: 'db', schema: 'app', table: 'orders', column: 'id', type: 'string', usage: 'filter', filterOperator: 'eq' }] },
      { endpointField: bodyField, mappingType: 'source_mapping', processingType: 'direct', sourceFields: [{ sourceType: 'database', sourceId: 'db', schema: 'app', table: 'orders', column: 'name', type: 'string', usage: 'write' }] }
    ]
  }
  const next = resetDraftForDatabaseOperation(draft, 'create')
  assert.equal(next.fieldMappings[0].mappingType === 'source_mapping' ? next.fieldMappings[0].sourceFields[0].usage : '', 'write')
  assert.equal(next.fieldMappings[0].mappingType === 'source_mapping' ? next.fieldMappings[0].sourceFields[0].filterOperator : undefined, undefined)
  assert.equal(defaultDatabaseUsageForOperation(pathField, 'delete'), 'filter')
  assert.deepEqual(validateApiDesignDraft(next), {})
})

/** 验证类型感知运算符和固定条件重复校验。 */
test('database operators and fixed conditions follow v4 rules', () => {
  assert.ok(allowedFilterOperators('string', 'varchar(100)').includes('contains'))
  assert.ok(allowedFilterOperators('integer', 'bigint').includes('gte'))
  assert.ok(allowedFilterOperators('array<number>', 'decimal').includes('between'))
  assert.ok(!allowedFilterOperators('string', 'varchar(100)').includes('between'))
  const response = field('response_body', 'id')
  response.side = 'response'
  const condition = { sourceType: 'database' as const, sourceId: 'db', schema: 'app', table: 'orders', column: 'deleted_at', type: 'datetime', operator: 'is_null' as const }
  const draft: WorkflowApiDesignDraft = {
    apiContractId: 'orders', endpointId: 'list', databaseOperation: 'read', databaseConditions: [condition, { ...condition }],
    fieldMappings: [{ endpointField: response, mappingType: 'source_mapping', processingType: 'direct', sourceFields: [{ sourceType: 'database', sourceId: 'db', schema: 'app', table: 'orders', column: 'id', type: 'integer', usage: 'read' }] }]
  }
  assert.match(validateApiDesignDraft(draft).__databaseConditions || '', /最多配置一个/)
  assert.equal(inferSelection({ ...draft, databaseConditions: [condition] }).selection?.sourceType, 'database')

  const valueCondition = { ...condition, column: 'amount', type: 'decimal', operator: 'between' as const, value: [1, 10] }
  assert.equal(validateApiDesignDraft({ ...draft, databaseConditions: [valueCondition] }).__databaseConditions, undefined)
  assert.match(validateApiDesignDraft({ ...draft, databaseConditions: [{ ...valueCondition, value: [10, 1] }] }).__databaseConditions || '', /不兼容/)
})
