import assert from 'node:assert/strict'
import { buildJsonSchema, readJsonSchemaMetadata } from '../src/renderer/src/components/DataSourcesPage/jsonSchema'
import { projectStoredJsonStructure } from '../src/renderer/src/components/DataSourcesPage/jsonStructure'
import type { DataSourceJsonStructure } from '../src/renderer/src/typings/dataSources'

const request = buildJsonSchema({ id: 0, items: [{ name: '商品' }] }, { '$["id"]': '请求编号', '$["items"][]["name"]': '商品名称' }, { '$["id"]': 'number' })!
assert.deepEqual(request, { type: 'object', properties: { id: { type: 'number', description: '请求编号' }, items: { type: 'array', items: { type: 'object', properties: { name: { type: 'string', description: '商品名称' } } } } } })
const response = buildJsonSchema({ id: '0' }, { '$["id"]': '响应编号' }, {})!
const requestDraft = readJsonSchemaMetadata(request)
const responseDraft = readJsonSchemaMetadata(response)
assert.equal(requestDraft.fieldTypes['$["id"]'], 'number')
assert.equal(responseDraft.fieldTypes['$["id"]'], 'string')
assert.equal(requestDraft.descriptions['$["id"]'], '请求编号')
assert.equal(responseDraft.descriptions['$["id"]'], '响应编号')
assert.deepEqual(buildJsonSchema({ id: 0, items: [{ name: '商品' }] }, requestDraft.descriptions, requestDraft.fieldTypes), request)
const projected = projectStoredJsonStructure(request)
assert.equal(projected.shape.children.id.kind, 'number')
assert.equal(projected.shape.children.items.arrayItem?.children.name.kind, 'string')
assert.equal(projected.descriptions['$["items"][]["name"]'], '商品名称')
assert.deepEqual(buildJsonSchema({ renamed: true }, requestDraft.descriptions, requestDraft.fieldTypes), { type: 'object', properties: { renamed: { type: 'boolean' } } })
assert.equal(buildJsonSchema(null, requestDraft.descriptions, requestDraft.fieldTypes), null)
assert.equal(buildJsonSchema(undefined, requestDraft.descriptions, requestDraft.fieldTypes), null)
assert.deepEqual(buildJsonSchema([], {}, {}), { type: 'array' })
assert.deepEqual(buildJsonSchema({}, {}, {}), { type: 'object', properties: {} })

const special = JSON.parse('{"a.b":{"q\\\"[]":1},"__proto__":{"field":true}}')
const specialSchema = buildJsonSchema(special, { '$["a.b"]["q\\\"[]"]': '特殊名称' }, {})!
assert.equal(specialSchema.properties?.['a.b'].properties?.['q"[]'].description, '特殊名称')
assert.equal(specialSchema.properties?.__proto__.properties?.field.type, 'boolean')
assert.equal(projectStoredJsonStructure(specialSchema).shape.children.__proto__.children.field.kind, 'boolean')
assert.equal(readJsonSchemaMetadata(specialSchema).descriptions['$["a.b"]["q\\\"[]"]'], '特殊名称')
const mixed = buildJsonSchema([{ id: 1 }, { id: '2' }, null], {}, {})!
assert.deepEqual(mixed.items?.type, ['object', 'null'])
assert.deepEqual(mixed.items?.properties?.id.type, ['integer', 'string'])

const lateItems = Array.from({ length: 35 }, (_, index) => index === 34 ? { late: '尾部字段' } : { id: index })
const late = buildJsonSchema(lateItems, { '$[]["late"]': '尾部说明' }, {})!
assert.equal(late.items?.properties?.late.description, '尾部说明')
let deep: unknown = { leaf: 1 }
for (let depth = 0; depth < 12; depth += 1) deep = { child: deep }
const completeDeep = buildJsonSchema(deep, {}, {})!
let deepNode = completeDeep
for (let depth = 0; depth < 12; depth += 1) deepNode = deepNode.properties!.child
assert.equal(deepNode.properties?.leaf.type, 'integer')
assert.equal(projectStoredJsonStructure(completeDeep).truncated, true)
const wide = buildJsonSchema(Object.fromEntries(Array.from({ length: 350 }, (_, index) => [`f-${index}`, index])), {}, {})!
const before = JSON.stringify(wide)
assert.equal(Object.keys(wide.properties!).length, 350)
assert.equal(projectStoredJsonStructure(wide).truncated, true)
assert.equal(JSON.stringify(wide), before)
const primitive: DataSourceJsonStructure = { type: 'number', description: '总数' }
assert.equal(projectStoredJsonStructure(primitive).shape.kind, 'number')
assert.equal(readJsonSchemaMetadata(primitive).descriptions.$, '总数')
