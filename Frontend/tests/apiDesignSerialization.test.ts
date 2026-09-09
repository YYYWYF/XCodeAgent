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
  applySourceMapping,
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
    sources: [{ id: 'orders-db', name: '订单库', type: 'database' }],
    draft: { apiContractId: 'orders-api', endpointId: 'orders.list', fieldMappings: [] }
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
  let next = replaceFieldMapping(source, createBusinessDescriptionMapping(field, '分页页码'))
  next = replaceFieldMapping(next, createBusinessDescriptionMapping(payload().endpointFields[1], '展示总数'))
  assert.equal(next.fieldMappings[0].mappingType, 'business_description')
  assert.deepEqual(validateApiDesignDraft(next), {})
  assert.match(apiDesignMappingPreview(next.fieldMappings[0]), /分页页码/)
})

/** 数据源映射只嵌入来源字段，不保存候选节点 ID。 */
test('source mapping embeds source field without ids', () => {
  const data = payload()
  const field = data.endpointFields[1]
  const source: WorkflowApiExternalFieldNode = {
    nodeType: 'source_field', id: 'ui-only', sourceType: 'external_api', sourceId: 'upstream',
    directoryId: 'catalog', operationId: 'list', section: 'response_body', path: 'total', type: 'number'
  }
  let draft = replaceFieldMapping(
    normalizeApiDesignDraft(data),
    createBusinessDescriptionMapping(data.endpointFields[0], '分页页码')
  )
  draft = applySourceMapping(draft, field, source)
  const mapping = findFieldMapping(draft, field)
  assert.equal(mapping?.mappingType, 'source_mapping')
  assert.equal('id' in (mapping && 'sourceFields' in mapping ? mapping.sourceFields[0] : {}), false)
  assert.deepEqual(validateApiDesignDraft(draft), {})
})

/** 来源映射支持单字段说明与多字段说明，并保留全部来源。 */
test('source mapping supports processing descriptions and multiple sources', () => {
  const data = payload()
  const normalized = normalizeApiDesignDraft(data)
  const response = data.endpointFields[1]
  const first: WorkflowApiExternalFieldNode = {
    nodeType: 'source_field', id: 'first', sourceType: 'external_api', sourceId: 'upstream',
    directoryId: 'catalog', operationId: 'list', section: 'response_body', path: 'data.total', type: 'number'
  }
  const second: WorkflowApiExternalFieldNode = {
    ...first, id: 'second', path: 'data.frozen'
  }
  let draft = applySourceMapping(normalized, response, first)
  const single = findFieldMapping(draft, response)
  if (!single || single.mappingType !== 'source_mapping') throw new Error('expected source mapping')
  draft = replaceFieldMapping(draft, {
    ...single,
    processingType: 'single_field_description',
    businessDescription: '将上游总额转换为展示金额。'
  })
  draft = replaceFieldMapping(draft, {
    endpointField: single.endpointField,
    mappingType: 'source_mapping',
    processingType: 'multi_field_description',
    sourceFields: [single.sourceFields[0], { ...single.sourceFields[0], path: 'data.frozen' }],
    businessDescription: '总额减去冻结金额。\n空值按业务错误处理。'
  })
  assert.deepEqual(validateApiDesignDraft(draft), {})
  assert.match(apiDesignMappingPreview(draft.fieldMappings[1]), /总额减去冻结金额/)
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
