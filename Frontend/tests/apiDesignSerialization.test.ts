import assert from 'node:assert/strict'
import { test } from 'node:test'
import type {
  WorkflowApiDesignPayload,
  WorkflowApiField,
  WorkflowApiExternalFieldNode
} from '../src/renderer/src/typings/workflow'
import {
  apiDesignFieldKey,
  apiDesignMappingPreview,
  createApiDesignAction,
  createBusinessDescriptionMapping,
  createUnconfiguredFieldMapping,
  findFieldMapping,
  normalizeApiDesignDraft,
  replaceFieldMapping,
  validateApiDesignDraft
} from '../src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/apiDesignSerialization'
import {
  applyDirectSourceMapping,
  applyEntityMapping,
  ensureTemplateEntityField,
  projectApiFieldMappingRows
} from '../src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/apiDesignTableModel'

/** 构造仅包含当前 fieldMappings 契约的 API 设计载荷。 */
function payload(): WorkflowApiDesignPayload {
  const request: WorkflowApiField = {
    nodeType: 'endpoint_field', id: 'endpoint:request:query:current', side: 'request',
    location: 'query', path: 'current', type: 'number', required: true, description: ''
  }
  const response: WorkflowApiField = {
    nodeType: 'endpoint_field', id: 'endpoint:response:response_body:total', side: 'response',
    location: 'response_body', path: 'total', type: 'number', required: false, description: ''
  }
  return {
    endpoint: { apiContractId: 'orders-api', id: 'orders.list', method: 'GET', path: '/orders' },
    endpointFields: [request, response],
    entityTemplates: [{ id: 'Order', name: '订单', fields: [{ name: 'id', type: 'number' }] }],
    sources: [{ id: 'orders-db', name: '订单库', type: 'database' }],
    draft: { apiContractId: 'orders-api', endpointId: 'orders.list', sceneEntities: [], fieldMappings: [] }
  }
}

/** 必填字段初始只产生一条未配置记录，且不生成节点或边。 */
test('normalize creates one self-contained row per endpoint field', () => {
  const draft = normalizeApiDesignDraft(payload())
  assert.equal(draft.fieldMappings.length, 2)
  assert.equal(draft.fieldMappings.filter((item) => item.mappingType === 'unconfigured').length, 2)
  assert.equal(findFieldMapping(draft, payload().endpointFields[0])?.endpointField.path, 'current')
})

/** 业务说明映射只携带 endpoint 字段快照和说明文本。 */
test('business description mapping is self-contained', () => {
  const source = normalizeApiDesignDraft(payload())
  const field = payload().endpointFields[0]
  const next = replaceFieldMapping(source, createBusinessDescriptionMapping(field, '分页页码'))
  assert.equal(next.fieldMappings[0].mappingType, 'business_description')
  assert.deepEqual(validateApiDesignDraft(next), {})
  assert.match(apiDesignMappingPreview(next.fieldMappings[0]), /分页页码/)
})

/** 直接来源映射只嵌入来源字段，不保存候选节点 ID。 */
test('direct source mapping embeds source field without ids', () => {
  const data = payload()
  const field = data.endpointFields[1]
  const source: WorkflowApiExternalFieldNode = {
    nodeType: 'source_field', id: 'ui-only', sourceType: 'external_api', sourceId: 'upstream',
    directoryId: 'catalog', operationId: 'list', section: 'response_body', path: 'total', type: 'number'
  }
  const draft = applyDirectSourceMapping(normalizeApiDesignDraft(data), field, source)
  const mapping = findFieldMapping(draft, field)
  assert.equal(mapping?.mappingType, 'direct_source')
  assert.equal('id' in (mapping && 'sourceField' in mapping ? mapping.sourceField : {}), false)
  assert.deepEqual(validateApiDesignDraft(draft), {})
})

/** 经实体映射可以同时保存实体引用和可选来源字段。 */
test('through entity mapping embeds entity and optional source', () => {
  const data = payload()
  const normalized = normalizeApiDesignDraft(data)
  const ensured = ensureTemplateEntityField(normalized, data.entityTemplates[0], 'id')
  const source: WorkflowApiExternalFieldNode = {
    nodeType: 'source_field', id: 'ui-only', sourceType: 'external_api', sourceId: 'upstream',
    directoryId: 'catalog', operationId: 'list', section: 'response_body', path: 'id', type: 'number'
  }
  const field = data.endpointFields[1]
  const draft = applyEntityMapping(ensured.draft, field, ensured.field, source)
  const mapping = findFieldMapping(draft, field)
  assert.equal(mapping?.mappingType, 'through_entity')
  assert.equal(mapping && mapping.mappingType === 'through_entity' ? mapping.entityField.path : '', 'id')
  assert.deepEqual(validateApiDesignDraft(draft), {})
})

/** 表格投影直接读取 fieldMappings，未配置字段按必填性显示状态。 */
test('table rows derive from fieldMappings', () => {
  const data = payload()
  const draft = normalizeApiDesignDraft(data)
  const rows = projectApiFieldMappingRows(draft, data, 'request')
  assert.equal(rows[0].key, apiDesignFieldKey(data.endpointFields[0]))
  assert.equal(rows[0].status, 'required_missing')
})

/** 当前确认动作只提交 fieldMappings，不再生成图结构。 */
test('confirmation action serializes current contract only', () => {
  const draft = normalizeApiDesignDraft(payload())
  const action = createApiDesignAction(draft, 'confirm')
  assert.equal(action.draft.fieldMappings.length, 2)
  assert.equal('nodes' in action.draft, false)
  assert.equal('mappings' in action.draft, false)
})

/** 旧图字段即使存在也不会被归一化读取。 */
test('legacy graph fields are ignored rather than migrated', () => {
  const data = payload()
  const draft = normalizeApiDesignDraft({
    ...data,
    draft: { ...data.draft, nodes: [], mappings: [] }
  } as unknown as WorkflowApiDesignPayload)
  assert.equal(draft.fieldMappings.length, 2)
  assert.equal('nodes' in draft, false)
})

/** 未配置构造器保持单条 endpoint 字段记录。 */
test('unconfigured helper keeps endpoint snapshot', () => {
  const field = payload().endpointFields[0]
  const mapping = createUnconfiguredFieldMapping(field)
  assert.equal(mapping.endpointField.path, 'current')
  assert.equal(mapping.mappingType, 'unconfigured')
})
