import assert from 'node:assert/strict'
import { test } from 'node:test'
import { debugKeyValueRows } from '../src/renderer/src/service/debugKeyValue'
import { renderToStaticMarkup } from 'react-dom/server'
import { debugObject, debugRequestPath, debugStringMap } from '../src/renderer/src/service/runtimeDebugRequest'
import type { DebugEndpoint, RuntimeAgent } from '../src/renderer/src/service/runtimeWorkbench'
import AgentDebugPanel from '../src/renderer/src/components/AiChatPanel/components/RuntimeAgentDetail/AgentDebugPanel'
import AgentDebugResponse from '../src/renderer/src/components/AiChatPanel/components/RuntimeAgentDetail/AgentDebugResponse'
import AgentDebugEvents from '../src/renderer/src/components/AiChatPanel/components/RuntimeAgentDetail/AgentDebugEvents'
import AgentDebugStreamView from '../src/renderer/src/components/AiChatPanel/components/RuntimeAgentDetail/AgentDebugStreamView'
import { initialDebugFields, readDebugFields } from '../src/renderer/src/components/AiChatPanel/components/RuntimeAgentDetail/AgentDebugRequestEditor'

const endpoint: DebugEndpoint = {
  apiContractId: 'detect', endpointId: 'run', label: '检测敏感数据', method: 'POST', path: '/api/detect/{id}',
  responseMode: 'json', kind: 'business', relation: 'agent_call', parameters: [{ name: 'q', in: 'query' }],
  requestSchema: { type: 'object', properties: { text: { type: 'string' } } }, responseSchema: { type: 'object' }
}

test('执行事件逐行展示全部记录，超过 40 条仍可查看首尾事件', () => {
  const events = Array.from({ length: 80 }, (_, index) => ({ type: `EVENT_${index}`, value: { delta: `片段${index}` } }))
  const html = renderToStaticMarkup(<AgentDebugEvents events={events} />)
  assert.match(html, /80 条 AG-UI 事件/)
  assert.match(html, /EVENT_0</)
  assert.match(html, /EVENT_79</)
  assert.equal((html.match(/data-row-key=/g) || []).length, 80)
})

test('请求编辑以正式接口为目标，路径和查询编码正确，页面上下文默认空', () => {
  const fields = initialDebugFields(endpoint)
  fields.pathParameters = debugKeyValueRows({ id: 'a b' })
  fields.query = debugKeyValueRows({ q: '假期' })
  const values = readDebugFields(fields)
  assert.deepEqual(values.context, {})
  assert.equal(debugRequestPath(endpoint, values), '/api/detect/a%20b?q=%E5%81%87%E6%9C%9F')
  assert.throws(() => debugRequestPath(endpoint, { ...values, pathParameters: {} }), /路径参数/)
  assert.throws(() => debugRequestPath(endpoint, { ...values, query: { unknown: '1' } }), /查询参数/)
})

test('JSON 和 Header 输入给出明确错误，不能覆盖调试身份或伪造交互响应', () => {
  assert.throws(() => debugObject('[]', 'Body'), /JSON 对象/)
  assert.throws(() => debugStringMap('{"x":1}', 'Headers'), /字符串/)
  assert.throws(() => debugStringMap('{"Authorization":"other"}', 'Headers'), /平台 Header/)
  assert.throws(() => readDebugFields({ ...initialDebugFields(endpoint), context: '{"interactionResponse":{}}' }), /上下文仅支持/)
})

test('JSON 调试显示接口、请求配置和响应入口，不依赖页面选择或聊天气泡', () => {
  const agent: RuntimeAgent = { agentId: 'assistant', name: '检测助手', purpose: '', pageIds: [], publicEndpoint: false,
    contractHash: 'test', debugEndpoints: [endpoint], interactionContract: { mode: 'task', inputSchema: {} },
    agentSettings: { prompt: {}, model: {}, memory: {}, tools: {}, knowledge: {}, skills: {}, context: {} } }
  const html = renderToStaticMarkup(<AgentDebugPanel agent={agent} workspaceRoot="/workspace" />)
  assert.match(html, /POST \/api\/detect\/\{id\}/)
  assert.match(html, /一次性响应/)
  assert.match(html, /Headers/)
  assert.match(html, /发送请求/)
  assert.doesNotMatch(html, /调试页面上下文|aria-label="调试对话"/)
  assert.match(html, /aria-label="调试调用接口"/)
  assert.match(html, /aria-label="JSON 请求体"/, '非流式 POST 默认展开 Body 编辑器')
})

test('同一智能体的流式接口显示问答区域，非流式接口显示请求体，空结构化结果不占空间', () => {
  const agent: RuntimeAgent = { agentId: 'assistant', name: '检测助手', purpose: '', pageIds: [], publicEndpoint: true,
    contractHash: 'test', debugEndpoints: [{ ...endpoint, path: '/api/chat', responseMode: 'ag-ui', kind: 'agent_runtime', relation: 'agent_entry' }, endpoint],
    interactionContract: { mode: 'conversation', inputSchema: {} },
    agentSettings: { prompt: {}, model: {}, memory: {}, tools: {}, knowledge: {}, skills: {}, context: {} } }
  const html = renderToStaticMarkup(<AgentDebugPanel agent={agent} workspaceRoot="/workspace" />)
  assert.match(html, /aria-label="调试调用接口"/)
  assert.match(html, /aria-label="调试对话"/)
  assert.doesNotMatch(html, /aria-label="JSON 请求体"/)
  const messages = renderToStaticMarkup(<AgentDebugStreamView name="助手" view={{ status: 'completed', messages: [], tools: [], result: null }}
    taskMode={false} ready busy={false} expired={false} restartRequired={false} draft="" onDraftChange={() => undefined}
    onSend={() => undefined} onNew={() => undefined} onStop={() => undefined} />)
  assert.doesNotMatch(messages, /结构化结果/)
})

test('没有已完成接口时提示先开发，不显示请求编辑器且禁止启动调试', () => {
  const agent: RuntimeAgent = { agentId: 'assistant', name: '检测助手', purpose: '', pageIds: [], publicEndpoint: false,
    contractHash: 'test', debugEndpoints: [], interactionContract: { mode: 'task', inputSchema: {} },
    agentSettings: { prompt: {}, model: {}, memory: {}, tools: {}, knowledge: {}, skills: {}, context: {} } }
  const html = renderToStaticMarkup(<AgentDebugPanel agent={agent} workspaceRoot="/workspace" />)
  assert.match(html, /暂无可调试接口，请先完成接口开发/)
  assert.match(html, /<button[^>]*disabled[^>]*><span>启动调试<\/span><\/button>/)
  assert.doesNotMatch(html, /发送请求|aria-label="JSON 请求体"|aria-label="调试对话"/)
})

test('非成功 HTTP 保留真实状态和响应正文，协议不一致明确提示', () => {
  const html = renderToStaticMarkup(<AgentDebugResponse response={{ statusCode: 422, elapsedMs: 42,
    headers: { 'content-type': 'application/json' }, body: '{"error":"缺少业务字段"}', protocolMismatch: true }} />)
  assert.match(html, /HTTP 422/)
  assert.match(html, /42 ms/)
  assert.match(html, /缺少业务字段/)
  assert.match(html, /Content-Type 与 JSON 契约不一致/)
})
