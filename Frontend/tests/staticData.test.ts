import assert from 'node:assert/strict'
import { test } from 'node:test'
import { staticFields, staticDraftError, staticDraftErrors, previewStaticResponse } from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/staticData'
import type { WorkflowApiDesignDraft } from '../src/renderer/src/typings/workflow'
import { parsePreviewInput, previewInputKey, staticPreviewInputs } from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/staticPreviewInputs'

/** 试填参数按身份去重，保留零值并与静态列表组合。 */
test('preview accepts typed request inputs without changing the draft', () => {
  const value = draft()
  const field = { ...value.fieldMappings[1].endpointField, side: 'request' as const, location: 'query' as const, path: 'name', type: 'integer' }
  value.fieldMappings[1] = { endpointField: value.fieldMappings[1].endpointField, mappingType: 'value_mapping', right: { kind: 'endpoint', endpointField: field } }
  const saved = JSON.stringify(value)
  assert.equal(staticPreviewInputs(value).length, 1)
  assert.throws(() => previewStaticResponse(value), /请填写接口参数/)
  assert.deepEqual(previewStaticResponse(value, { [previewInputKey(field)]: parsePreviewInput(field, '0') }), { list: [{ id: 0, name: 0 }, { id: 2, name: 0 }] })
  assert.equal(JSON.stringify(value), saved)
  assert.equal(parsePreviewInput({ ...field, type: 'boolean' }, 'false'), false)
  assert.equal(parsePreviewInput({ ...field, type: 'string' }, '""'), '')
  assert.throws(() => parsePreviewInput(field, '1.5'), /类型/)
  assert.throws(() => parsePreviewInput(field, ''), /请填写/)
  assert.notEqual(previewInputKey(field), previewInputKey({ ...field, location: 'path' }))
})

/** 含业务逻辑的映射不执行，也不返回部分预览。 */
test('business rules block preview before parameter collection', () => {
  const value = draft()
  value.fieldMappings[1] = { endpointField: value.fieldMappings[1].endpointField, mappingType: 'business_description', businessDescription: '生成名称' }
  assert.throws(() => staticPreviewInputs(value), /业务规则/)
  assert.throws(() => previewStaticResponse(value), /业务规则/)
})

/** 构造不会改变接口契约的静态列表返回映射。 */
function draft(prefix = 'list'): WorkflowApiDesignDraft {
  return { apiContractId: 'test', endpointId: 'list', sourceBinding: { sourceType: 'static' }, staticData: [{ id: 0, name: 'A' }, { id: 2, name: 'B' }],
    fieldMappings: ['id', 'name'].map((key) => ({ mappingType: 'source_mapping', processingType: 'direct',
      endpointField: { side: 'response', location: 'response_body', path: `${prefix}[].${key}`, type: key === 'id' ? 'integer' : 'string', required: true, description: '' },
      sourceFields: [{ sourceType: 'static', path: `[].${key}`, type: key === 'id' ? 'integer' : 'string' }] })) }
}

/** 列表逐项映射，保留零值、顺序与根数组形状。 */
test('static preview preserves arrays and zero', () => {
  assert.deepEqual(previewStaticResponse(draft()), { list: [{ id: 0, name: 'A' }, { id: 2, name: 'B' }] })
  assert.deepEqual(previewStaticResponse(draft('')), [{ id: 0, name: 'A' }, { id: 2, name: 'B' }])
})

/** 固定值应用于每个列表元素，不覆盖其他直连字段。 */
test('fixed value repeats per item', () => {
  const value = draft()
  value.fieldMappings[1] = { endpointField: value.fieldMappings[1].endpointField, mappingType: 'value_mapping', right: { kind: 'fixed', value: '固定名称' } }
  assert.deepEqual(previewStaticResponse(value), { list: [{ id: 0, name: '固定名称' }, { id: 2, name: '固定名称' }] })
})

/** 结构变更及列表层级不匹配必须在确认前暴露。 */
test('stale paths and cardinality are rejected', () => {
  const value = draft()
  value.staticData = [{ code: 1 }]
  assert.match(staticDraftError(value), /失效/)
  const scalar = draft()
  scalar.fieldMappings[0].endpointField.path = 'id'
  assert.match(staticDraftError(scalar), /层级/)
  assert.match(staticDraftError(scalar), /返回字段「id」是单值/)
  assert.match(staticDraftError(scalar), /静态字段「\[\]\.id」来自列表/)
  assert.throws(() => previewStaticResponse(scalar), /JSON 对象/)
  scalar.fieldMappings[1].endpointField.path = 'name'
  const errors = staticDraftErrors(scalar)
  assert.match(errors['response:response_body:id'], /「id」是单值/)
  assert.match(errors['response:response_body:name'], /「name」是单值/)
  assert.equal(errors.__staticData, undefined)
})

/** 单个对象直接映射与固定值可共同组成有效响应。 */
test('object response supports direct fields with fixed values', () => {
  const value = draft()
  value.staticData = { id: 1, name: '商品' }
  value.fieldMappings.forEach((mapping) => {
    mapping.endpointField.path = mapping.endpointField.path.replace('list[].', '')
    if (mapping.mappingType === 'source_mapping') mapping.sourceFields = staticFields(value.staticData).filter((field) => field.sourceType === 'static' && field.path === mapping.endpointField.path)
  })
  value.fieldMappings.push({ endpointField: { ...value.fieldMappings[1].endpointField, path: 'code' }, mappingType: 'value_mapping', right: { kind: 'fixed', value: 'code' } })
  assert.equal(staticDraftError(value), '')
  assert.deepEqual(previewStaticResponse(value), { id: 1, name: '商品', code: 'code' })
})

/** 严格拒绝类型不一致、原型键和无法推断的空数据。 */
test('invalid data cannot produce source fields', () => {
  for (const value of [[], {}, null, [{ id: 1 }, { id: '1' }], JSON.parse('{"__proto__":1}')]) assert.throws(() => staticFields(value))
  assert.equal(staticFields({ active: false })[0].type, 'boolean')
})
