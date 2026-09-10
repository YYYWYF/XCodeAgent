import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { compileTsx } from '../src/renderer/src/components/DesignRenderer/compileTsx'
import { AgentChatCore } from '../src/renderer/design-runtime/agent-ui/AgentChatCore'
import { AgentConversationTemplate } from '../src/renderer/design-runtime/agent-ui/AgentConversationTemplate'
import { AgentFloatingPanelTemplate } from '../src/renderer/design-runtime/agent-ui/AgentFloatingPanelTemplate'
import {
  AGENT_UI_TEMPLATE_VERSION,
  buildAgentSurfaceEvidence,
  clampAndSnapFloatingPosition,
  parseAgentUiTemplateConfig
} from '../src/renderer/design-runtime/agent-ui'

assert.equal(AGENT_UI_TEMPLATE_VERSION, 'agent-ui.v1')

const standaloneEvidence = buildAgentSurfaceEvidence({
  agentId: 'order_assistant',
  actionId: 'open_order_assistant',
  surface: 'standalone_page'
})
assert.deepEqual(standaloneEvidence, {
  agentId: 'order_assistant',
  actionId: 'open_order_assistant',
  surface: 'standalone_page',
  templateModule: '@xcodeagent/agent-ui-design',
  templateVersion: 'agent-ui.v1'
})

assert.deepEqual(
  clampAndSnapFloatingPosition(
    { x: 930, y: -20 },
    { width: 1000, height: 800 },
    { width: 56, height: 56 },
    16
  ),
  { x: 928, y: 16 }
)

const parsedConfig = parseAgentUiTemplateConfig(
  JSON.stringify({
    templateVersion: 'agent-ui.v1',
    agentId: 'order_assistant',
    surface: 'standalone_page',
    name: '订单助手',
    responsibility: '分析订单并协助跟进',
    actionId: 'open_order_assistant',
    contextItems: [],
    capabilities: [{ id: 'analyze_orders', label: '分析订单' }],
    suggestedQuestions: ['本周有哪些异常订单？'],
    features: { attachments: false, approvals: true, tools: true, maximize: false },
    mock: {
      userMessage: '分析本周订单',
      assistantMessage: '我会先检查异常订单。',
      toolTitle: '查询订单',
      toolDetail: '已读取 18 条订单。',
      approvalTitle: '需要确认',
      approvalDetail: '是否继续？',
      successMessage: '分析完成',
      errorMessage: '模拟连接中断'
    }
  })
)
assert.equal(parsedConfig.agentId, 'order_assistant')
assert.throws(
  () => parseAgentUiTemplateConfig('{"templateVersion":"agent-ui.v1"}'),
  /Agent UI 配置字段集合无效/
)
assert.throws(
  () => parseAgentUiTemplateConfig(JSON.stringify({ ...parsedConfig, unknown: true })),
  /Agent UI 配置字段集合无效/
)

const normalChatMarkup = renderToStaticMarkup(
  createElement(AgentChatCore, { config: parsedConfig, state: 'normal' })
)
assert.match(normalChatMarkup, /我会先检查异常订单。/)

const errorChatMarkup = renderToStaticMarkup(
  createElement(AgentChatCore, { config: parsedConfig, state: 'error' })
)
assert.match(errorChatMarkup, /本次运行未完成/)
assert.doesNotMatch(errorChatMarkup, /我会先检查异常订单。/)

for (const state of [
  'empty',
  'loading',
  'running',
  'stopped',
  'tool',
  'approval',
  'success'
] as const) {
  const stateMarkup = renderToStaticMarkup(
    createElement(AgentChatCore, { config: parsedConfig, state })
  )
  assert.ok(stateMarkup.length > 0, state)
}

const standaloneMarkup = renderToStaticMarkup(
  createElement(AgentConversationTemplate, { configJson: JSON.stringify(parsedConfig) })
)
const floatingMarkup = renderToStaticMarkup(
  createElement(AgentFloatingPanelTemplate, { configJson: JSON.stringify(parsedConfig) })
)
assert.doesNotMatch(standaloneMarkup, /切换模拟状态/)
assert.doesNotMatch(floatingMarkup, /切换模拟状态/)

const compiled = compileTsx(`
  import React from 'react'
  import { AgentConversationTemplate } from '@xcodeagent/agent-ui-design'
  export default function Demo() { return <AgentConversationTemplate config={{}} /> }
`)
assert.match(compiled, /window\.__DESIGN_RUNTIME__\.agentUi/)
assert.doesNotMatch(compiled, /require\(['"]@xcodeagent\/agent-ui-design['"]\)/)

console.log('agent UI design runtime tests passed')
