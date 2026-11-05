import assert from 'node:assert/strict'
import { test } from 'node:test'
import { renderToStaticMarkup } from 'react-dom/server'
import { checkDebugJson } from '../src/renderer/src/service/debugJsonValidation'
import DebugJsonEditor from '../src/renderer/src/components/AiChatPanel/components/RuntimeAgentDetail/DebugJsonEditor'
import { debugRequestIssues, initialDebugFields } from '../src/renderer/src/components/AiChatPanel/components/RuntimeAgentDetail/AgentDebugRequestEditor'
import type { DebugEndpoint } from '../src/renderer/src/service/runtimeWorkbench'

const schema = { type: 'object', required: ['employeeId', 'days'], additionalProperties: false,
  properties: { employeeId: { type: 'string', minLength: 1 }, days: { type: 'integer', minimum: 1 },
    reason: { type: 'string' }, lines: { type: 'array', items: { type: 'object', required: ['name'], properties: { name: { type: 'string' } } } } } }

test('JSON 语法错误带解析器可定位的行列，空输入不能冒充空对象', () => {
  const result = checkDebugJson('{\n "employeeId":\n}')
  assert.equal(result.syntaxValid, false)
  assert.match(result.errors[0], /行.*列|JSON 语法错误/)
  assert.equal(checkDebugJson('').syntaxValid, false)
  assert.equal(checkDebugJson('{}').syntaxValid, true)
})

test('按当前 Schema 校验必填、类型、数组和未知字段，不自动转换或补值', () => {
  const missing = checkDebugJson('{}', { schema })
  assert.equal(missing.syntaxValid, true)
  assert.equal(missing.errors.length, 2)
  assert.match(missing.errors.join(','), /employeeId.*days/)
  assert.match(checkDebugJson('{"employeeId":"demo","days":"2"}', { schema }).errors.join(','), /\/days：应为整数/)
  assert.match(checkDebugJson('{"employeeId":"demo","days":0}', { schema }).errors.join(','), /\/days/)
  assert.match(checkDebugJson('{"employeeId":"demo","days":1,"lines":[{}]}', { schema }).errors.join(','), /\/lines\/0.*name/)
  assert.match(checkDebugJson('{"employeeId":"demo","days":1,"other":true}', { schema }).errors.join(','), /未声明字段/)
  const valid = checkDebugJson('{"employeeId":"demo","days":2}', { schema })
  assert.deepEqual(valid.errors, [])
  assert.deepEqual(valid.value, { employeeId: 'demo', days: 2 })
})

test('Draft 2020 本地引用和日期格式按标准库校验', () => {
  const referenced = { $defs: { code: { type: 'string', pattern: '^EMP[0-9]+$' } }, type: 'object',
    properties: { employeeId: { $ref: '#/$defs/code' }, date: { type: 'string', format: 'date' } } }
  assert.equal(checkDebugJson('{"employeeId":"EMP1","date":"2026-11-05"}', { schema: referenced }).errors.length, 0)
  assert.ok(checkDebugJson('{"employeeId":"oops","date":"tomorrow"}', { schema: referenced }).errors.length >= 2)
})

test('编辑器展示实时错误和格式化入口，发送前复用相同的请求校验', () => {
  const html = renderToStaticMarkup(<DebugJsonEditor label="JSON 请求体" value="{}" schema={schema} onChange={() => undefined} />)
  assert.match(html, /aria-invalid="true"/)
  assert.match(html, /请填写必填字段/)
  assert.match(html, /格式化/)
  const endpoint: DebugEndpoint = { apiContractId: 'api', endpointId: 'create', label: '申请', method: 'POST', path: '/api/request',
    relation: 'agent_call', kind: 'business', responseMode: 'json', parameters: [], requestSchema: schema, responseSchema: null }
  const fields = initialDebugFields(endpoint)
  assert.equal(debugRequestIssues(endpoint, fields, '{}', true).length, 2)
  assert.deepEqual(debugRequestIssues(endpoint, fields, '{"employeeId":"demo","days":1}', true), [])
  assert.ok(debugRequestIssues(endpoint, { ...fields, headers: [{ id: 'reserved', key: 'Authorization', value: 'other', enabled: true }] }, '{"employeeId":"demo","days":1}', true).length)
})

test('DDL 检测必填错误只展示字段和中文提示，不泄露校验库英文', () => {
  const result = checkDebugJson('{}', { schema: { type: 'object', required: ['table_name', 'ddl_statement'],
    properties: { table_name: { type: 'string' }, ddl_statement: { type: 'string' } } } })
  assert.deepEqual(result.errors, ['请填写必填字段：table_name。', '请填写必填字段：ddl_statement。'])
  assert.doesNotMatch(result.errors.join(''), /Instance|required property|Expected/)
})
