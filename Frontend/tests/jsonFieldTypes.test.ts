import assert from 'node:assert/strict'
import { convertJsonFieldType, convertJsonValue, matchesJsonFieldType, normalizeJsonFieldTypes } from '../src/renderer/src/components/DataSourcesPage/jsonFieldTypes'
import { mergeExternalSourceChanges, requireOperationDetails, validateOperationParameters } from '../src/renderer/src/components/DataSourcesPage/dataSourceOperations'
import type { DataSourceFieldType, DataSourceOperation, DataSourceParameter, ExternalApiDataSource } from '../src/renderer/src/typings/dataSources'

const conversions: [unknown, DataSourceFieldType, unknown][] = [
  ['123', 'number', 123], [' -1.5 ', 'integer', -1], ['2e2', 'number', 200],
  ['0x10', 'number', 0], ['', 'number', 0], ['Infinity', 'number', 0], ['01', 'number', 0],
  [true, 'number', 0], [9007199254740992, 'integer', 0], [Infinity, 'number', 0],
  [false, 'boolean', false], [' TRUE ', 'boolean', true], ['false', 'boolean', false],
  [0, 'boolean', false], [1, 'boolean', true], [2, 'boolean', false],
  [true, 'string', 'true'], [12, 'string', '12'], [null, 'string', ''],
  [{ x: 1 }, 'string', ''], [3, 'object', {}], [3, 'array', []], [{ x: 1 }, 'null', null]
]
for (const [value, type, expected] of conversions) assert.deepEqual(convertJsonValue(value, type), expected)
assert.equal(matchesJsonFieldType(true, 'integer'), false)
assert.equal(matchesJsonFieldType(true, 'number'), false)
assert.equal(matchesJsonFieldType(0, 'number'), true)
assert.equal(matchesJsonFieldType(null, 'object'), false)
assert.equal(matchesJsonFieldType([], 'object'), false)

const source = { items: Array.from({ length: 35 }, (_, index) => ({ value: String(index) })) }
const before = JSON.stringify(source)
const converted = convertJsonFieldType(source, '$["items"][]["value"]', 'integer')
assert.equal(converted.matchedCount, 35)
assert.equal(converted.changedCount, 35)
assert.equal(converted.destructiveCount, 0)
assert.equal((converted.value as { items: { value: number }[] }).items[34].value, 34)
assert.equal(JSON.stringify(source), before)

const containers = { items: [{ value: { id: 1 } }, { value: [1, 2] }, { other: 3 }] }
const containerBefore = JSON.stringify(containers)
const destructive = convertJsonFieldType(containers, '$["items"][]["value"]', 'string')
assert.equal(destructive.destructiveCount, 2)
assert.equal(destructive.matchedCount, 2)
assert.deepEqual(destructive.value, { items: [{ value: '' }, { value: '' }, { other: 3 }] })
// 转换结果尚未提交时，原始草稿保持不变；取消确认只需丢弃转换结果。
assert.equal(JSON.stringify(containers), containerBefore)
const sameContainer = convertJsonFieldType(containers, '$', 'object')
assert.equal(sameContainer.destructiveCount, 0)
assert.equal(sameContainer.value, containers)

const special = JSON.parse('{"a.b":{"q\\\"[]": "42"},"__proto__":{"x":"8"}}')
assert.deepEqual((convertJsonFieldType(special, '$["a.b"]["q\\\"[]"]', 'number').value as Record<string, unknown>)['a.b'], { 'q"[]': 42 })
const prototypeConversion = convertJsonFieldType(special, '$["__proto__"]["x"]', 'integer').value as Record<string, unknown>
assert.equal(Object.hasOwn(prototypeConversion, '__proto__'), true)
assert.deepEqual(prototypeConversion.__proto__, { x: 8 })
assert.equal(Object.getPrototypeOf(prototypeConversion), Object.prototype)
assert.equal(convertJsonFieldType({ x: 1 }, '$["missing"]', 'array').matchedCount, 0)
assert.deepEqual(convertJsonFieldType([[{ id: '1' }], [{ id: '2' }]], '$[][]["id"]', 'integer').value, [[{ id: 1 }], [{ id: 2 }]])

let deepSample: unknown = { value: '6' }
for (let index = 0; index < 12; index += 1) deepSample = { child: deepSample }
const deepPath = '$' + '["child"]'.repeat(12) + '["value"]'
const deepConverted = convertJsonFieldType(deepSample, deepPath, 'integer')
assert.equal(deepConverted.matchedCount, 1)
assert.deepEqual(normalizeJsonFieldTypes(deepConverted.value, { [deepPath]: 'integer' }), { [deepPath]: 'integer' })

const metadataSample = { id: 0, items: [{ id: 1 }, { id: '2' }] }
const fieldTypes: Record<string, DataSourceFieldType> = { '$["id"]': 'number', '$["items"][]["id"]': 'integer', '$["missing"]': 'object' }
assert.deepEqual(normalizeJsonFieldTypes(metadataSample, fieldTypes), { '$["id"]': 'number' })
assert.deepEqual(normalizeJsonFieldTypes({ id: '0' }, fieldTypes), {})
assert.deepEqual(normalizeJsonFieldTypes(null, fieldTypes), {})
assert.deepEqual(normalizeJsonFieldTypes(undefined, fieldTypes), {})
assert.deepEqual(normalizeJsonFieldTypes({ items: [] }, { '$["items"][]': 'number' }), {})
assert.deepEqual(normalizeJsonFieldTypes({ id: 1 }, { '$["id"]': 'integer' }), { '$["id"]': 'integer' })
assert.deepEqual(normalizeJsonFieldTypes({ id: '1' }, { '$["id"]': 'string' }), { '$["id"]': 'string' })

const pathParameter: DataSourceParameter = { name: 'id', type: 'integer', required: true, description: 'ID' }
const queryParameter: DataSourceParameter = { name: 'id', type: 'array', required: false, description: '' }
validateOperationParameters('/items/{id}', [pathParameter], [queryParameter])
assert.throws(() => validateOperationParameters('/items', [pathParameter], []), /一一对应/)
assert.throws(() => validateOperationParameters('/items/{id}', [], []), /一一对应/)
assert.throws(() => validateOperationParameters('/items/{id}', [{ ...pathParameter, required: false }], []), /必填/)
assert.throws(() => validateOperationParameters('/items/{id}', [{ ...pathParameter, type: 'object' }], []), /基础类型/)
assert.throws(() => validateOperationParameters('/items', [], [queryParameter, { ...queryParameter, name: 'ID' }]), /重复/)
assert.throws(() => validateOperationParameters('/items/{}', [], []), /格式无效/)
assert.throws(() => validateOperationParameters('/items/{id}', [pathParameter], Array.from({ length: 50 }, (_, index) => ({ ...queryParameter, name: `q-${index}` }))), /合计/)

const operation: DataSourceOperation = {
  id: 'op-a', name: '查询', method: 'GET', path: '/items/{id}', pathParameters: [pathParameter], queryParameters: [queryParameter],
  headers: [{ name: 'X-Version', value: '2' }], requestSample: { id: 0 }, responseSample: { id: '1' },
  requestStructure: { type: 'object', properties: { id: { type: 'number', description: '请求编号' } } },
  responseStructure: { type: 'object', properties: { id: { type: 'string', description: '响应编号' } } }
}
const otherOperation: DataSourceOperation = { ...operation, id: 'op-b', name: '另一个接口' }
const latest: ExternalApiDataSource = {
  id: 'domain', type: 'external_api', name: '域名', baseUrl: 'api.example.com', baseUrlConfigKey: 'services.api', timeoutMs: 32000,
  headers: [{ name: 'X-Shared', value: 'keep' }], directories: [
    { id: 'dir-a', name: '一', operations: [operation, otherOperation] },
    { id: 'dir-b', name: '二', operations: [] }
  ]
}
const latestBefore = JSON.stringify(latest)
// 模拟详情切换后接口再次变成列表摘要，历史加载标记不能让它覆盖完整配置。
const summaryOperation: DataSourceOperation = { id: operation.id, name: operation.name, method: operation.method, path: operation.path, pathParameters: [], queryParameters: [], headers: [], requestStructure: null, responseStructure: null }
const candidate: ExternalApiDataSource = {
  ...latest, timeoutMs: 10000, headers: [], baseUrlConfigKey: undefined,
  directories: [{ id: 'dir-a', name: '重命名', operations: [{ ...summaryOperation, id: 'op-b' }] }, { id: 'dir-b', name: '二', operations: [summaryOperation] }]
}
const merged = mergeExternalSourceChanges(latest, candidate)
assert.equal(merged.timeoutMs, 32000)
assert.equal(merged.baseUrlConfigKey, 'services.api')
assert.deepEqual(merged.headers, latest.headers)
assert.equal(merged.directories[0].name, '重命名')
assert.deepEqual(merged.directories[0].operations[0], otherOperation)
assert.deepEqual(merged.directories[1].operations[0], operation)
const edited: DataSourceOperation = { ...operation, name: '已编辑', requestSample: { id: '1' }, requestStructure: { type: 'object', properties: { id: { type: 'string', description: '请求编号' } } } }
const editMerged = mergeExternalSourceChanges(latest, candidate, edited)
assert.deepEqual(editMerged.directories[1].operations[0], edited)
assert.deepEqual(editMerged.directories[0].operations[0], otherOperation)
assert.equal(JSON.stringify(latest), latestBefore)
assert.throws(() => mergeExternalSourceChanges(latest, { ...candidate, id: 'other' }), /域名不一致/)
assert.deepEqual(merged.directories[1].operations[0].pathParameters, [pathParameter])
assert.deepEqual(merged.directories[1].operations[0].queryParameters, [queryParameter])
assert.equal('parameters' in merged.directories[1].operations[0], false)
assert.equal(requireOperationDetails({ sources: [latest] }, 'domain', 'op-a'), operation)
assert.throws(() => requireOperationDetails({ sources: [latest] }, 'other-domain', 'op-a'), /不存在/)
assert.throws(() => requireOperationDetails({ sources: [latest] }, 'domain', 'missing-operation'), /不存在/)
