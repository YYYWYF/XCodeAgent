import assert from 'node:assert/strict'
import { test } from 'node:test'
import './agentSurfaceDraft.test'
import { renderToStaticMarkup } from 'react-dom/server'
import { projectWorkbenchAgents } from '../src/main/agentPlanningArtifactProjection'
import { buildQuickTasks } from '../src/renderer/src/components/AiChatPanel/components/QuickTaskGuide/quickTasks'
import { developmentArtifactTotals } from '../src/renderer/src/developmentArtifacts'
import { compileTsx } from '../src/renderer/src/components/DesignRenderer/compileTsx'
import ApplicationOutline from '../src/renderer/src/components/AiChatPanel/components/ApplicationOutline'
import AgentDevelopmentDetail from '../src/renderer/src/components/AiChatPanel/components/AgentDevelopmentDetail'
import TechnicalPlanAgentSection from '../src/renderer/src/components/AiChatPanel/components/DocPanel/TechnicalPlanAgentSection'
import RequirementAgentSection from '../src/renderer/src/components/AiChatPanel/components/DocPanel/RequirementAgentSection'
import { RequirementPagesSection } from '../src/renderer/src/components/AiChatPanel/components/DocPanel/RequirementDocSections'

const product = {
  agents: [{ agentId: 'assistant', capabilities: [{ name: '解释库存' }], boundaries: ['只读'] }]
}
const contract = {
  agentId: 'assistant',
  name: '库存助手',
  purpose: '解释库存状态。',
  pageIds: ['home'],
  agentSettings: {
    prompt: { systemPrompt: '请解释当前库存。' },
    model: { source: 'project' },
    memory: { isolation: 'principal/thread' },
    tools: { enabled: false, bindings: [] },
    skills: { enabled: false },
    knowledge: { enabled: false },
    context: { trusted: true }
  }
}
const agents = projectWorkbenchAgents(product, { agent_contracts: [contract] })

test('当前 Runtime 投影不依赖 Java Gateway 并保留七模块事实', () => {
  assert.equal(agents[0].label, '库存助手')
  assert.deepEqual(agents[0].tools, [])
  assert.deepEqual(agents[0].capabilities, ['解释库存'])
  assert.equal(Object.keys(agents[0].agentSettings).length, 7)
  assert.throws(
    () => projectWorkbenchAgents({ agents: [] }, { agent_contracts: [contract] }),
    /缺少唯一产品规划/
  )
})

test('快捷开发任务保留独立 Agent 身份，不伪造页面或 Endpoint', () => {
  const tasks = buildQuickTasks([], [], [], undefined, agents)
  assert.equal(tasks.length, 1)
  assert.equal(tasks[0].kind, 'agent')
  assert.equal(tasks[0].id, 'agent:assistant')
})

test('智能体完成统计和设计稿运行时使用当前真实合同', () => {
  assert.deepEqual(
    developmentArtifactTotals({
      pages: {},
      endpoints: {},
      entities: {},
      agents: {
        assistant: { initialDevelopmentStatus: 'pending' }
      }
    }),
    { total: 1, completed: 0 }
  )
  assert.match(
    compileTsx(
      "import { AgentConversationTemplate } from '@xcodeagent/agent-ui-design'; export default AgentConversationTemplate"
    ),
    /__DESIGN_RUNTIME__\.agentUi/
  )
})

test('工作台菜单显示智能体及其选中状态', () => {
  const markup = renderToStaticMarkup(
    <ApplicationOutline
      agents={agents}
      selectedAgentId="assistant"
      apiContracts={[]}
      entities={[]}
      pages={[]}
      pageTree={[]}
      outlineLocked={false}
      selectedApiEndpointKey=""
      selectedEntityId=""
      selectedPageId=""
      onApiEndpointSelect={() => undefined}
      onEntitySelect={() => undefined}
      onPageSelect={() => undefined}
    />
  )
  assert.match(markup, /库存助手/)
  assert.match(markup, /aria-pressed="true"/)
})

test('智能体详情提供正式开发和配置入口并展示七模块', () => {
  const markup = renderToStaticMarkup(
    <AgentDevelopmentDetail
      agent={agents[0]}
      workspaceRoot="/test-only"
      disabled={false}
      onStart={() => undefined}
      onRefresh={() => undefined}
    />
  )
  for (const label of [
    '开发智能体',
    '编辑配置',
    '提示词',
    '模型',
    '记忆',
    '工具',
    '技能',
    '知识',
    '上下文'
  ])
    assert.ok(markup.includes(label), label)
})

test('技术规划审阅显示真实 Prompt，不把 JSON 作为用户编辑产物', () => {
  const markup = renderToStaticMarkup(
    <TechnicalPlanAgentSection plan={{ agent_contracts: [contract] }} />
  )
  assert.match(markup, /请解释当前库存/)
  assert.doesNotMatch(markup, /&quot;systemPrompt&quot;:/)
})

test('智能体页签只读展示中文页面名称，不再提供浮窗开关', () => {
  const markup = renderToStaticMarkup(
    <RequirementAgentSection
      spec={{}}
      productPlan={{
        confirmation_status: 'confirmed',
        pages: [{ pageId: 'home', name: '库存首页' }],
        agents: [
          {
            agentId: 'assistant',
            name: '库存助手',
            pageActionBindings: [
              { pageId: 'home', surface: { type: 'floating_panel', enabled: true } }
            ]
          }
        ]
      }}
    />
  )
  assert.match(markup, /库存首页/)
  assert.match(markup, /浮窗已开启/)
  assert.doesNotMatch(markup, /role="switch"/)
  assert.doesNotMatch(markup, /ant-descriptions/)
})

test('页面卡片按绑定显示浮窗开关，独立对话页面不提供开关', () => {
  const markup = renderToStaticMarkup(
    <RequirementPagesSection
      sectionKey="pages"
      spec={{}}
      onToggleAgentSurface={() => undefined}
      productPlan={{
        pages: [
          { pageId: 'home', name: '库存首页' },
          { pageId: 'chat', name: '对话页面' },
          { pageId: 'plain', name: '普通页面' }
        ],
        agents: [
          {
            agentId: 'assistant',
            name: '库存助手',
            pageActionBindings: [
              { pageId: 'home', surface: { type: 'floating_panel', enabled: false } },
              { pageId: 'chat', surface: { type: 'standalone_page', enabled: true } }
            ]
          }
        ]
      }}
    />
  )
  assert.equal((markup.match(/role="switch"/g) || []).length, 1)
  assert.match(markup, /aria-label="库存首页集成库存助手浮窗"/)
  assert.match(markup, /aria-checked="false"/)
  assert.match(markup, /独立对话页面/)
  assert.doesNotMatch(markup, /disabled/)
})

test('页面浮窗没有编辑权限或保存中时不可操作', () => {
  for (const busy of [false, true]) {
    const markup = renderToStaticMarkup(
      <RequirementPagesSection
        sectionKey="pages"
        spec={{}}
        agentSurfaceBusy={busy}
        onToggleAgentSurface={busy ? () => undefined : undefined}
        productPlan={{
          pages: [{ pageId: 'home', name: '首页' }],
          agents: [
            {
              agentId: 'assistant',
              name: '助手',
              pageActionBindings: [
                { pageId: 'home', surface: { type: 'floating_panel', enabled: true } }
              ]
            }
          ]
        }}
      />
    )
    assert.match(markup, /aria-checked="true"/)
    assert.match(markup, /disabled/)
  }
})
