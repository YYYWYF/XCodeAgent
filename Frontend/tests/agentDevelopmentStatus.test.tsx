import assert from 'node:assert/strict'
import { test } from 'node:test'
import { renderToStaticMarkup } from 'react-dom/server'
import AgentOutlineSection from '../src/renderer/src/components/AiChatPanel/components/ApplicationOutline/AgentOutlineSection'
import ApplicationOutline from '../src/renderer/src/components/AiChatPanel/components/ApplicationOutline'
import ApiOutlineGroup from '../src/renderer/src/components/AiChatPanel/components/ApplicationOutline/ApiOutlineGroup'
import EndpointDesignResult from '../src/renderer/src/components/AiChatPanel/components/EndpointDesignResult'
import { developmentPlanningTotals } from '../src/renderer/src/developmentArtifacts'
import type { BusinessAgentOption } from '../src/shared/businessAgent'
import type { DevelopmentArtifacts, DevelopmentPlanningApiContract, DevelopmentPlanningPageOption } from '../src/renderer/src/typings'

const agent: BusinessAgentOption = {
  agentId: 'assistant', label: '假勤助手', purpose: '查询本人假期及公司政策',
  pageIds: [], executionMode: 'conversation', publicEndpoint: true,
  capabilities: [], boundaries: [], tools: [], agentSettings: {}
}

test('目录顺序为页面、智能体、接口、实体', () => {
  const html = renderToStaticMarkup(<ApplicationOutline agents={[agent]} pages={[]} pageTree={[]}
    apiContracts={[]} entities={[]} outlineLocked={false} selectedPageId="" selectedApiEndpointKey=""
    selectedEntityId="" onPageSelect={() => undefined} onApiEndpointSelect={() => undefined}
    onEntitySelect={() => undefined} />)
  const labels = ['页面', '智能体', '接口', '实体'].map((label) => html.indexOf(`>${label}</span>`))
  assert.ok(labels.every((position) => position >= 0))
  assert.deepEqual([...labels].sort((left, right) => left - right), labels)
})

test('智能体分组共用完成圆点及完整计数，移除手动刷新按钮', () => {
  for (const status of ['pending', 'in_progress', 'completed'] as const) {
    const html = renderToStaticMarkup(<AgentOutlineSection agents={[agent]} query=""
      developmentArtifacts={{ pages: {}, endpoints: {}, entities: {}, agents: {
        assistant: { initialDevelopmentStatus: status }
      } }} />)
    assert.ok(html.includes(`data-status="${status}"`))
    assert.ok(html.includes(`>${status === 'completed' ? 1 : 0}/1</span>`))
    assert.doesNotMatch(html, /刷新/)
  }
})

test('搜索隐藏条目仍保留全量计数，变更后的智能体不显示完成绿色', () => {
  const artifacts: DevelopmentArtifacts = { pages: {}, endpoints: {}, entities: {}, agents: {
    assistant: { initialDevelopmentStatus: 'completed', changeStatus: 'changed', changeReason: '工具发生变化' }
  } }
  const html = renderToStaticMarkup(<AgentOutlineSection agents={[agent]} query="" developmentArtifacts={artifacts} />)
  assert.match(html, /data-status="changed"/)
  assert.match(html, /工具发生变化/)
  assert.match(html, />0\/1<\/span>/)
  const hidden = renderToStaticMarkup(<AgentOutlineSection agents={[agent]} query="不匹配" developmentArtifacts={artifacts} />)
  assert.match(hidden, />0\/1<\/span>/)
  assert.match(hidden, /没有匹配的智能体/)
  assert.doesNotMatch(hidden, /刷新/)
})

test('五页面六接口及一智能体全部完成时顶部为12/12，派生接口读取同一能力记录', () => {
  const completed = { initialDevelopmentStatus: 'completed' as const, changeStatus: 'unchanged' as const }
  const pages: DevelopmentPlanningPageOption[] = Array.from({ length: 5 }, (_, index) => ({
    pageId: `page${index}`, key: `page${index}`, label: `页面${index}`, path: `/page${index}`, purpose: '', designed: true
  }))
  const contracts: DevelopmentPlanningApiContract[] = [{ id: 'business', label: '业务接口',
    endpoints: Array.from({ length: 5 }, (_, index) => ({
      id: `query${index}`, kind: 'business', method: 'GET', path: `/query${index}`, summary: ''
    })) }, { id: 'agent.assistant', label: '智能体运行接口', endpoints: [{
      id: 'agent.assistant.run', kind: 'agent_runtime', agent_id: 'assistant', method: 'POST',
      path: '/agents/assistant/run', summary: ''
    }] }]
  const artifacts: DevelopmentArtifacts = {
    pages: Object.fromEntries(pages.map((page) => [page.pageId, completed])),
    endpoints: { business: Object.fromEntries(contracts[0].endpoints.map((endpoint) => [endpoint.id, completed])) },
    agents: { assistant: completed }, entities: {}
  }
  assert.deepEqual(developmentPlanningTotals(pages, contracts, artifacts, [agent]), { completed: 12, total: 12 })
  artifacts.agents!.assistant = { ...completed, changeStatus: 'changed', changeReason: '工具发生变化' }
  assert.deepEqual(developmentPlanningTotals(pages, contracts, artifacts, [agent]), { completed: 10, total: 12 })
})

test('非流式智能体调用入口使用机器人图标，完成状态仍来自接口自身开发记录', () => {
  const endpoint = { id: 'detect.start', kind: 'business' as const, name: '开始检测',
    agentCall: { agentId: 'assistant', responseMode: 'json' as const },
    method: 'POST', path: '/api/detect/start', summary: '' }
  const artifacts: DevelopmentArtifacts = { pages: {}, entities: {}, endpoints: {}, agents: {
    assistant: { initialDevelopmentStatus: 'completed' }
  } }
  /** 渲染真实目录，检查图标、圆点及计数使用同一份接口事实。 */
  const render = (): string => renderToStaticMarkup(<ApiOutlineGroup
    contract={{ id: 'detect', label: '/api/detect', endpoints: [endpoint] }} allEndpoints={[endpoint]}
    developmentArtifacts={artifacts} expanded selectedKey="" onToggle={() => undefined} onSelect={() => undefined} />)
  const pending = render()
  assert.match(pending, /data-icon="robot"/)
  assert.match(pending, /智能体已完成，接口待开发/)
  assert.match(pending, /data-status="pending"/)
  assert.match(pending, />0\/1<\/span>/)
  artifacts.endpoints.detect = { 'detect.start': { initialDevelopmentStatus: 'completed', changeStatus: 'unchanged' } }
  const completed = render()
  assert.match(completed, /data-status="completed"/)
  assert.match(completed, />1\/1<\/span>/)
})

test('智能体业务接口详情展示调用契约及JSON返回，不展示空数据库映射', () => {
  const html = renderToStaticMarkup(<EndpointDesignResult detail={{ apiContractId: 'detect', endpointId: 'detect.start',
    status: 'confirmed', designed: true, reason: '', design: { agentCall: {
      agentId: 'assistant', responseMode: 'json', inputBindings: { '/table_name': '/body/table_name' },
      resultBindings: { '/risk': '/risk' }
    } } }} />)
  assert.match(html, /业务接口智能体调用设计/)
  assert.match(html, /一次性 JSON 业务响应/)
  assert.match(html, /table_name/)
  assert.doesNotMatch(html, /无数据库映射|暂无映射字段/)
})
