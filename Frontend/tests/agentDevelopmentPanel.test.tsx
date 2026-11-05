import assert from 'node:assert/strict'
import { test } from 'node:test'
import { renderToStaticMarkup } from 'react-dom/server'
import { Children, isValidElement, type ReactNode, type ReactElement } from 'react'
import './sourceFileNavigation.test'
import type { RuntimeAgent } from '../src/renderer/src/service/runtimeWorkbench'
import type { WorkbenchExecution } from '../src/renderer/src/typings'
import AgentDevelopmentStatus from '../src/renderer/src/components/AiChatPanel/components/RuntimeAgentDetail/AgentDevelopmentStatus'
import { agentDevelopmentPresentation } from '../src/renderer/src/components/AiChatPanel/components/RuntimeAgentDetail/agentDevelopmentPresentation'

/** 提供无关联页面、工具启用而知识/技能未启用的当前合同，验证进度不依赖页面交付。 */
function fixture(): RuntimeAgent {
  return {
    agentId: 'assistant', name: '假勤助手', purpose: '查询本人假期余额', pageIds: [],
    contractHash: 'test', publicEndpoint: true, debugEndpoints: [], interactionContract: { mode: 'conversation', inputSchema: {} },
    agentSettings: {
      prompt: { systemPrompt: '查询本人假期余额' }, model: { source: 'project' }, memory: {}, context: {},
      tools: { enabled: true, bindings: [{ toolId: 'query_balance', apiContractId: 'balance', endpointId: 'list' }] },
      skills: { enabled: false }, knowledge: { enabled: false }
    },
    development: {
      progress: { initialDevelopmentStatus: 'completed', changeStatus: 'unchanged' },
      modules: ['prompt', 'model', 'memory', 'tools', 'skills', 'knowledge', 'context'].map((module) => ({
        module, label: module, status: 'completed', files: ['agent-runtime/src/app/agent/factory.py']
      }))
    }
  }
}

/** 构造当前任务记录，复现历史完成与当前失败/待确认同时存在的场景。 */
function execution(status: WorkbenchExecution['status']): WorkbenchExecution {
  return { status, scope: 'agent', targetId: 'assistant', threadId: 'thread', runId: 'run', phase: 'build',
    startedAt: '2026-11-05T09:00:00Z', updatedAt: '2026-11-05T09:00:00Z' }
}

test('未启用模块不计入进度，智能体完成不依赖关联页面', () => {
  const view = agentDevelopmentPresentation(fixture())
  assert.equal(view.readyCount, 5)
  assert.equal(view.items.length, 5)
  assert.equal(view.modules.length, 7)
  assert.equal(view.state, 'completed')
  assert.equal(view.canDebug, true)
})

test('当前失败或等待确认优先于历史完成，保留原任务处理入口', () => {
  for (const status of ['failed', 'awaiting_user'] as const) {
    const agent = fixture()
    agent.development!.execution = execution(status)
    const view = agentDevelopmentPresentation(agent)
    assert.equal(view.state, status)
    assert.equal(view.completed, true)
    assert.equal(view.canDebug, false)
    const html = renderToStaticMarkup(<AgentDevelopmentStatus agent={agent} onOpen={() => undefined} />)
    assert.match(html, /打开当前任务/)
    assert.doesNotMatch(html, /开始调试/)
  }
})

test('正式输入变化和状态不可判定均不能显示开发已完成', () => {
  for (const changeStatus of ['changed', 'unavailable'] as const) {
    const agent = fixture()
    agent.development!.progress!.changeStatus = changeStatus
    const view = agentDevelopmentPresentation(agent)
    assert.equal(view.state, changeStatus)
    assert.equal(view.canDebug, false)
  }
})

test('开发事项全部满足仍不能代替正式完成登记', () => {
  const agent = fixture()
  agent.development!.progress = null
  assert.equal(agentDevelopmentPresentation(agent).state, 'pending')
  agent.development!.modules[0].status = 'running'
  assert.equal(agentDevelopmentPresentation(agent).state, 'running')
  agent.development!.modules[0].status = 'failed'
  assert.equal(agentDevelopmentPresentation(agent).state, 'failed')
})

test('首屏展示业务名称与调试入口，源码默认折叠，不伪造复用和验证结果', () => {
  const html = renderToStaticMarkup(<AgentDevelopmentStatus agent={fixture()} onDebug={() => undefined}
    apiContracts={[{ id: 'balance', label: '余额', endpoints: [{ id: 'list', kind: 'business',
      method: 'POST', path: '/api/balance', summary: '查询本人假期余额' }] }]} />)
  assert.match(html, /开始调试/)
  assert.match(html, /查询本人假期余额/)
  assert.match(html, /1 个已配置工具/)
  assert.match(html, /5\/5 项已完成/)
  assert.match(html, /<details class="agent-development-implementation">/)
  assert.doesNotMatch(html, /<details[^>]*\bopen/)
  assert.doesNotMatch(html, /验证通过|复用已有实现|服务运行中/)
})

test('空开发记录可开始开发，未配置工具明确显示空态', () => {
  const agent = fixture()
  agent.development = undefined
  agent.agentSettings.tools = { enabled: false, bindings: [] }
  const html = renderToStaticMarkup(<AgentDevelopmentStatus agent={agent} onStart={async () => undefined} />)
  assert.match(html, /开始开发智能体/)
  assert.match(html, /尚无开发事项记录/)
  assert.match(html, /当前智能体未配置业务工具/)
  assert.doesNotMatch(html, /role="progressbar"/)
})

test('已满足统一展示已完成，保留内部状态与计数语义', () => {
  const agent = fixture()
  agent.development!.modules[2].status = 'already_satisfied'
  const html = renderToStaticMarkup(<AgentDevelopmentStatus agent={agent} />)
  assert.doesNotMatch(html, /已满足/)
  assert.match(html, /data-status="already_satisfied">已完成/)
  assert.equal(agentDevelopmentPresentation(agent).readyCount, 5)
  assert.equal(agent.development!.modules[2].status, 'already_satisfied')
})

/** 遍历组件返回的元素，直接验证文件按钮传递完整路径而非默认页面文件。 */
function fileButtons(node: ReactNode): ReactElement[] {
  const result: ReactElement[] = []
  Children.forEach(node, (child) => {
    if (!isValidElement(child)) return
    if (child.props['aria-label']?.startsWith('查看文件 ')) result.push(child)
    result.push(...fileButtons(child.props.children))
  })
  return result
}

test('每条实现文件均可跳转对应路径，未连接文件面板时按钮禁用', () => {
  const agent = fixture()
  const target = 'agent-runtime/src/app/persistence/checkpointer.py'
  agent.development!.modules[2].files = [target]
  let selected = ''
  const buttons = fileButtons(AgentDevelopmentStatus({ agent, onOpenFile: (path) => { selected = path } }))
  const button = buttons.find((item) => item.props['aria-label'] === `查看文件 ${target}`)!
  assert.equal(button.props.disabled, false)
  button.props.onClick()
  assert.equal(selected, target)
  assert.ok(fileButtons(AgentDevelopmentStatus({ agent })).every((item) => item.props.disabled))
})
