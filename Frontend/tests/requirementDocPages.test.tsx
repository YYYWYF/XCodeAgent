import assert from 'node:assert/strict'
import { test } from 'node:test'
import { renderToStaticMarkup } from 'react-dom/server'
import { RequirementPagesSection } from '../src/renderer/src/components/AiChatPanel/components/DocPanel/RequirementDocSections'
import type { JsonRecord } from '../src/renderer/src/components/AiChatPanel/components/DocPanel/RequirementDocPanelData'
import ProductPlanDocPanel from '../src/renderer/src/components/AiChatPanel/components/DocPanel/ProductPlanDocPanel'
import TechnicalPlanDocPanel from '../src/renderer/src/components/AiChatPanel/components/DocPanel/TechnicalPlanDocPanel'
import TechnicalPlanAgentSection from '../src/renderer/src/components/AiChatPanel/components/DocPanel/TechnicalPlanAgentSection'
import { technicalPlanDisplayContracts } from '../src/renderer/src/components/AiChatPanel/components/DocPanel/TechnicalPlanAgentContracts'
import TechnicalPlanSummary from '../src/renderer/src/components/Welcome/TechnicalPlanSummary'
import { PageBindingsSection } from '../src/renderer/src/components/AiChatPanel/components/DocPanel/TechnicalPlanPageBindings'
import './sourcePlanningDocument.test'

test('纯聊天应用展示两个启用入口共用的智能体接口，忽略关闭的入口', () => {
  const html = renderToStaticMarkup(
    <PageBindingsSection
      pages={['home', 'chat', 'disabled'].map((pageId) => ({ pageId, references: {} }))}
      contracts={[]}
      agents={[{ agentId: 'assistant', name: '通用聊天助手', pageIds: ['home', 'chat', 'disabled'] }]}
      productPlan={{
        pages: [
          { pageId: 'home', name: '展示首页', actions: [{ actionId: 'open', name: '浮窗聊天' }] },
          { pageId: 'chat', name: '独立聊天页', actions: [{ actionId: 'send', name: '发送消息' }] }
        ],
        agents: [
          {
            agentId: 'assistant',
            pageActionBindings: [
              { pageId: 'home', actionIds: ['open'], surface: { enabled: true } },
              { pageId: 'chat', actionIds: ['send'], surface: { enabled: true } },
            { pageId: 'disabled', actionIds: ['disabled_action'], surface: { enabled: false } }
            ]
          }
        ]
      }}
      sectionKey="page-bindings"
    />
  )
  for (const text of ['展示首页', '独立聊天页', '浮窗聊天', '发送消息', '通用聊天助手', 'AG-UI']) {
    assert.ok(html.includes(text), `缺少智能体绑定：${text}`)
  }
  assert.equal(html.match(/>POST \/agents\/assistant\/run<\/code>/g)?.length, 2)
  assert.ok(!html.includes('暂无页面绑定'))
  assert.ok(!html.includes('disabled_action'))
})

test('无智能体应用仍展示原业务接口绑定，静态应用仍展示原空态', () => {
  const props = {
    pages: [
      {
        pageId: 'orders',
        references: { endpoint_dependencies: [{ endpoint_id: 'list', trigger: '进入页面' }] }
      }
    ],
    contracts: [{ endpoints: [{ id: 'list', method: 'POST', path: '/orders/search' }] }],
    productPlan: { pages: [{ pageId: 'orders', name: '订单列表' }] },
    sectionKey: 'page-bindings' as const
  }
  const html = renderToStaticMarkup(<PageBindingsSection {...props} />)
  for (const text of ['订单列表', '进入页面', 'POST /orders/search']) assert.ok(html.includes(text))
  assert.ok(!html.includes('AG-UI'))
  const empty = renderToStaticMarkup(<PageBindingsSection {...props} pages={[]} />)
  assert.ok(empty.includes('暂无页面绑定'))
})

/** 一份包含所有可选区块的页面规划，用于确认重做后内容一条不少。 */
function productPlan(): JsonRecord {
  return {
    pages: [
      {
        pageId: 'hello_world',
        name: 'Hello World',
        path: '/page/hello-world',
        module_id: 'mode_project_mgmt',
        description: '展示写死的字符串的欢迎页面',
        goal: '向用户展示固定的欢迎信息',
        information_items: [{ itemId: 't1', label: '欢迎信息', description: '固定字符串' }],
        actions: [
          {
            actionId: 'a1',
            name: '刷新',
            description: '重新加载',
            behavior: { type: 'submit', expectedResult: '内容刷新' }
          }
        ],
        navigation_targets: ['/page/home'],
        state_requirements: { loading: '短暂显示加载态', error: '不适用' },
        acceptance_criteria: ['能看到欢迎语', '不依赖后端']
      }
    ]
  }
}

test('页面卡不再展示原始 module_id', () => {
  const html = renderToStaticMarkup(
    <RequirementPagesSection productPlan={productPlan()} sectionKey="pages" spec={{}} />
  )
  assert.ok(!html.includes('mode_project_mgmt'), '不应出现原始 module_id')
})

test('页面卡重做后全部内容仍在，且带上序号与磁贴', () => {
  const html = renderToStaticMarkup(
    <RequirementPagesSection productPlan={productPlan()} sectionKey="pages" spec={{}} />
  )
  for (const text of [
    'Hello World',
    '/page/hello-world',
    '展示写死的字符串的欢迎页面',
    '页面目标',
    '向用户展示固定的欢迎信息',
    '业务信息',
    '欢迎信息',
    '核心操作',
    '刷新',
    '页面跳转',
    '/page/home',
    '状态要求',
    '短暂显示加载态',
    '产品验收标准',
    '能看到欢迎语',
    '不依赖后端'
  ]) {
    assert.ok(html.includes(text), `重做后缺少内容：${text}`)
  }
  assert.ok(html.includes('requirement-doc-page-index'), '缺少序号徽章')
  assert.ok(html.includes('requirement-doc-page-tile'), '业务信息应是磁贴')
  assert.ok(html.includes('requirement-doc-page-zone'), '缺少内嵌区块')
  assert.ok(html.includes('is-muted'), '规则区应带弱化标记')
})

test('独立产品规划复用页面可视化，并保留摘要、状态、版本和产品级验收标准', () => {
  const html = renderToStaticMarkup(
    <ProductPlanDocPanel
      productPlan={{
        ...productPlan(),
        app: { name: '欢迎应用', summary: '单页欢迎应用摘要' },
        confirmation_status: 'confirmed',
        version: '0.1.0',
        product_acceptance_criteria: ['应用可正常打开']
      }}
    />
  )
  for (const text of [
    '欢迎应用',
    '单页欢迎应用摘要',
    '已确认',
    '0.1.0',
    '页面与用户操作',
    '向用户展示固定的欢迎信息',
    '内容刷新',
    '能看到欢迎语',
    '产品级验收标准',
    '应用可正常打开'
  ]) {
    assert.ok(html.includes(text), `产品规划缺少内容：${text}`)
  }
  assert.ok(html.includes('requirement-doc-page-tile'), '产品规划应复用设计阶段的页面卡片')
})

test('空产品规划展示现有空态，待确认状态仍可见', () => {
  const html = renderToStaticMarkup(
    <ProductPlanDocPanel productPlan={{ confirmation_status: 'pending_user_confirmation' }} />
  )
  for (const text of ['未命名应用', '待确认', '暂无页面规划', '暂无产品级验收标准']) {
    assert.ok(html.includes(text), `空产品规划缺少内容：${text}`)
  }
})

test('技术规划沿用计划阶段的章节导航与无接口空态', () => {
  const html = renderToStaticMarkup(<TechnicalPlanDocPanel plan={{}} productPlan={{}} />)
  for (const text of ['架构', '实体', 'API 契约', '页面绑定', 'technical-plan-doc-panel']) {
    assert.ok(html.includes(text), `技术规划缺少章节：${text}`)
  }
})

test('无业务实体的两个聊天入口展示同一个 AG-UI 接口，不修改正式业务契约', () => {
  const plan = {
    entities: [],
    api_contracts: [],
    agent_contracts: [
      {
        agentId: 'agent_general_chat',
        name: '通用聊天助手',
        purpose: '通用问答',
        pageIds: ['page_home', 'page_agent_chat'],
        agentSettings: {}
      }
    ]
  }
  const before = JSON.stringify(plan)
  const html = renderToStaticMarkup(<TechnicalPlanDocPanel plan={plan} productPlan={{}} />)
  for (const text of [
    '通用聊天助手',
    'POST',
    '/agents/agent_general_chat/run',
    'AG-UI',
    'SSE',
    'RunAgentInput',
    'BaseEvent',
    'agent-runtime',
    'page_home',
    'page_agent_chat'
  ]) {
    assert.ok(html.includes(text), `智能体公开接口缺少：${text}`)
  }
  assert.doesNotMatch(html, /暂无 API 契约|暂无接口|Schema 引用暂不可解析|Bearer/)
  assert.equal((html.match(/technical-plan-contract-main/g) || []).length, 1)
  assert.equal(JSON.stringify(plan), before)
  const agentHtml = renderToStaticMarkup(<TechnicalPlanAgentSection plan={plan} />)
  assert.match(agentHtml, /\/agents\/agent_general_chat\/run/)
  const summaryHtml = renderToStaticMarkup(<TechnicalPlanSummary plan={plan} />)
  assert.match(summaryHtml, /\/agents\/agent_general_chat\/run/)
})

test('混合应用保留普通业务契约，每个智能体只增加一个只读接口分组', () => {
  const business = { id: 'order_api', entity_ids: ['Order'], endpoints: [{ id: 'order_api.list' }] }
  const plan = {
    api_contracts: [business],
    agent_contracts: [
      { agentId: 'assistant', name: '助手', pageIds: ['home', 'chat'] },
      { agentId: 'reviewer', name: '审核助手', pageIds: ['review'] }
    ]
  }
  const result = technicalPlanDisplayContracts(plan)
  assert.equal(result.length, 3)
  assert.equal(result[0], business)
  assert.deepEqual(
    result.map((item) => item.id),
    ['order_api', 'agent.assistant.run', 'agent.reviewer.run']
  )
  assert.equal(plan.api_contracts.length, 1)
})
