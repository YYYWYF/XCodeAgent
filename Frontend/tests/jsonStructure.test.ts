import assert from 'node:assert/strict'
import { collectJsonFieldPaths, inferJsonStructure, jsonPropertyPath, normalizeJsonFieldDescriptions, parseJsonSampleText } from '../src/renderer/src/components/DataSourcesPage/jsonStructure'
import './jsonFieldTypes.test'

const nested = inferJsonStructure({ user: { id: 1, name: 'Ada' }, items: [{ name: 'one' }, { name: 'two', active: true }] })
assert.equal(nested.shape.kind, 'object')
assert.deepEqual(nested.shape.childOrder, ['user', 'items'])
assert.equal(nested.shape.children.user.kind, 'object')
assert.equal(nested.shape.children.items.kind, 'array<object>')
assert.deepEqual(nested.shape.children.items.arrayItem?.childOrder, ['name', 'active'])
assert.equal(inferJsonStructure([]).shape.kind, 'array（空）')
assert.equal(inferJsonStructure(null).shape.kind, 'null')
assert.equal(inferJsonStructure('text').shape.kind, 'string')
assert.equal(inferJsonStructure(1).shape.kind, 'integer')
assert.equal(inferJsonStructure(1.25).shape.kind, 'number')
assert.equal(inferJsonStructure([1, 1.25]).shape.kind, 'array<integer | number>')
assert.equal(inferJsonStructure(JSON.parse('{"__proto__":{"x":1}}')).shape.children.__proto__.kind, 'object')

const specialSample = { 'filter.value': { 'a[]': true }, items: [{ sku: 'A1' }, { sku: 'A2', price: 1 }] }
assert.equal(jsonPropertyPath('$', 'filter.value'), '$["filter.value"]')
assert.equal(collectJsonFieldPaths(specialSample).has('$["items"][]["sku"]'), true)
assert.deepEqual(
  normalizeJsonFieldDescriptions(specialSample, {
    '$["filter.value"]': '过滤条件',
    '$["items"][]["sku"]': '商品编码',
    '$["removed"]': '应被清理'
  }),
  { '$["filter.value"]': '过滤条件', '$["items"][]["sku"]': '商品编码' }
)

const valid = parseJsonSampleText('{"items":[1,true]}')
assert.deepEqual(valid.value, { items: [1, true] })
assert.equal(parseJsonSampleText('  ').value, undefined)
assert.match(parseJsonSampleText('{broken').error || '', /JSON 格式有误/)

process.stdout.write('json structure tests passed\n')
