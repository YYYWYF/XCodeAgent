import assert from 'node:assert/strict'
import React from 'react'
import { act, create } from 'react-test-renderer'
import { AgentFloatingPanel } from '../../Backend/app/builtin_templates/agent-ui-frontend/src/components/AgentConversation/AgentFloatingPanel'
import type { AgentUiTemplateConfig } from '../../Backend/app/builtin_templates/agent-ui-frontend/src/typings/agentConversation'

// 只隔离 Ant Design 的 DOM 与聊天内核；被测浮窗使用真实 React 状态、effect 和卸载/重挂。
Object.assign(globalThis, { window: { innerWidth: 1280, innerHeight: 800,
  addEventListener() {}, removeEventListener() {}, setTimeout } })
const config = { name: '订单助手', templateVersion: 'agent-ui.v1', features: { maximize: false } } as AgentUiTemplateConfig
let view!: ReturnType<typeof create>
act(() => { view = create(<AgentFloatingPanel config={config} adapter={{} as any} />) })
act(() => { view.root.find((node) => node.type === 'div' && node.props['aria-label'] === '打开订单助手').props.onClick() })
act(() => { view.root.findByProps({ 'data-core-create': true }).props.onClick() })
assert.equal(view.root.findByProps({ 'data-core-thread': 'kept-thread' }).props['data-core-thread'], 'kept-thread')
act(() => { view.root.find((node) => node.type === 'div' && node.props['aria-label'] === '关闭会话面板').props.onClick() })
assert.equal(view.root.findAllByProps({ 'data-core-thread': 'kept-thread' }).length, 0)
act(() => { view.root.find((node) => node.type === 'div' && node.props['aria-label'] === '打开订单助手').props.onClick() })
assert.equal(view.root.findByProps({ 'data-core-thread': 'kept-thread' }).props['data-core-thread'], 'kept-thread')
act(() => { view.unmount() })
console.log('Floating panel: close/reopen retains the same thread identity')
