import assert from 'node:assert/strict'
import { test } from 'node:test'
import { renderToStaticMarkup } from 'react-dom/server'
import { debugKeyValueMap, debugKeyValueRows } from '../src/renderer/src/service/debugKeyValue'
import DebugKeyValueTable from '../src/renderer/src/components/AiChatPanel/components/RuntimeAgentDetail/DebugKeyValueTable'
import { debugRequestIssues, initialDebugFields, readDebugFields } from '../src/renderer/src/components/AiChatPanel/components/RuntimeAgentDetail/AgentDebugRequestEditor'
import type { DebugEndpoint } from '../src/renderer/src/service/runtimeWorkbench'

test('只发送启用的键值行，停用行内容保留且不覆盖启用值', () => {
  const rows = debugKeyValueRows({ employeeId: 'demo', ignored: 'keep' })
  rows[1].enabled = false
  assert.deepEqual(debugKeyValueMap(rows, '查询参数'), { employeeId: 'demo' })
  assert.equal(rows[1].value, 'keep')
  assert.deepEqual(debugKeyValueMap([...rows, { id: 'blank', key: '', value: '', enabled: true }], '查询参数'), { employeeId: 'demo' })
})

test('启用的重复 Key 和缺少 Key 不能静默发送，Headers 按大小写不敏感识别重复', () => {
  const rows = debugKeyValueRows({ 'X-Demo': 'one', 'x-demo': 'two' })
  assert.throws(() => debugKeyValueMap(rows, 'Headers'), /重复 Key/)
  assert.throws(() => debugKeyValueMap([{ id: 'missing', key: '', value: 'data', enabled: true }], 'Headers'), /缺少 Key/)
  rows[1].enabled = false
  assert.deepEqual(debugKeyValueMap(rows, 'Headers'), { 'X-Demo': 'one' })
})

test('现成 Table 提供启用、Key、Value 和增删入口，字段发送仍消费正式接口契约', () => {
  const rows = debugKeyValueRows({ 'X-Trace': 'demo' })
  const html = renderToStaticMarkup(<DebugKeyValueTable label="Headers" rows={rows} onChange={() => undefined} />)
  assert.match(html, /Key/)
  assert.match(html, /Value/)
  assert.match(html, /启用 Headers X-Trace/)
  assert.match(html, /添加行/)
  assert.match(html, /删除 Headers X-Trace/)
  const endpoint: DebugEndpoint = { apiContractId: 'test', endpointId: 'list', label: '查询', method: 'GET', path: '/api/items/{id}',
    relation: 'tool', kind: 'business', responseMode: 'json', parameters: [{ name: 'q', in: 'query' }], requestSchema: null, responseSchema: null }
  const fields = initialDebugFields(endpoint)
  fields.pathParameters[0].value = '123'
  fields.query = debugKeyValueRows({ q: 'demo' })
  fields.headers = rows
  assert.deepEqual(readDebugFields(fields), { pathParameters: { id: '123' }, query: { q: 'demo' }, headers: { 'X-Trace': 'demo' }, context: {} })
  fields.query[0].enabled = false
  assert.deepEqual(readDebugFields(fields).query, {})
  assert.deepEqual(debugRequestIssues(endpoint, fields, '', false), [])
})
