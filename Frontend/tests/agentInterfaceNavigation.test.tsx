import assert from 'node:assert/strict'
import { test } from 'node:test'
import { renderToStaticMarkup } from 'react-dom/server'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import ApplicationOutline from '../src/renderer/src/components/AiChatPanel/components/ApplicationOutline'
import AgentInterfaceRows from '../src/renderer/src/components/AiChatPanel/components/ApplicationOutline/AgentInterfaceRows'
import { matchingAgentInterfaces } from '../src/renderer/src/components/AiChatPanel/components/ApplicationOutline/outlineUtils'
import AgentInterfaceDetail from '../src/renderer/src/components/AiChatPanel/components/DevelopmentArtifactsPanel/AgentInterfaceDetail'
import { useDevelopmentArtifactDetail } from '../src/renderer/src/components/AiChatPanel/hooks/useDevelopmentArtifactDetail'
import { developmentPlanningTotals } from '../src/renderer/src/developmentArtifacts'
import type { BusinessAgentOption } from '../src/shared/businessAgent'
import QuickTaskGuide from '../src/renderer/src/components/AiChatPanel/components/QuickTaskGuide'

const assistant: BusinessAgentOption = {
  agentId: 'assistant',
  label: '通用聊天助手',
  purpose: '两个页面共用对话能力',
  pageIds: ['home', 'chat'],
  capabilities: [],
  boundaries: [],
  agentSettings: {},
  tools: []
}
const artifacts = {
  pages: {},
  endpoints: {},
  entities: {},
  agents: { assistant: { initialDevelopmentStatus: 'completed' as const } }
}

test('左侧接口快捷卡包含智能体接口和普通接口，保留状态与调用路径', () => {
  const props = {
    agents: [assistant],
    developmentArtifacts: artifacts,
    pages: [],
    entities: [],
    apiContracts: [
      {
        id: 'orders',
        label: '订单',
        endpoints: [
          {
            id: 'list',
            method: 'GET',
            path: '/orders',
            summary: '查询订单'
          }
        ]
      }
    ],
    disabled: false,
    loading: false,
    onStart: async (): Promise<void> => undefined
  }
  const html = renderToStaticMarkup(<QuickTaskGuide {...props} />)
  const apiSection = html.slice(html.indexOf('>接口</strong>'))
  assert.match(apiSection, /通用聊天助手/)
  assert.match(apiSection, /智能体接口/)
  assert.match(apiSection, /POST \/agents\/assistant\/run/)
  assert.match(apiSection, /AG-UI \/ SSE/)
  assert.match(apiSection, /已初次完成/)
  assert.match(apiSection, /GET/)
  assert.match(apiSection, /\/orders/)
  assert.match(apiSection, />2<\/span>/)
  assert.doesNotMatch(apiSection, /项目计划中暂无接口/)
  const disabled = renderToStaticMarkup(<QuickTaskGuide {...props} disabled />)
  assert.match(disabled.slice(disabled.indexOf('>接口</strong>')), /disabled=""/)
})

test('左侧仅有智能体时接口分类非空，没有任何接口时保留空态', () => {
  for (const agents of [[assistant], []]) {
    const html = renderToStaticMarkup(
      <QuickTaskGuide
        agents={agents}
        apiContracts={[]}
        pages={[]}
        entities={[]}
        disabled={false}
        loading={false}
        onStart={async () => undefined}
      />
    )
    const apiSection = html.slice(html.indexOf('>接口</strong>'))
    if (agents.length) {
      assert.match(apiSection, /智能体接口/)
      assert.match(apiSection, />1<\/span>/)
      assert.doesNotMatch(apiSection, /项目计划中暂无接口/)
    } else {
      assert.match(apiSection, /项目计划中暂无接口/)
    }
  }
})

test('接口分类展示一个带标识的智能体接口，状态复用 Agent 且总完成度不重复计数', () => {
  const html = renderToStaticMarkup(
    <ApplicationOutline
      agents={[assistant]}
      developmentArtifacts={artifacts}
      apiContracts={[]}
      entities={[]}
      pages={[]}
      pageTree={[]}
      outlineLocked={false}
      selectedPageId=""
      selectedApiEndpointKey=""
      selectedEntityId=""
      onPageSelect={() => undefined}
      onApiEndpointSelect={() => assert.fail('智能体接口不进入普通接口映射')}
      onEntitySelect={() => undefined}
    />
  )
  const apiSection = html.slice(html.indexOf('>接口</span>'), html.indexOf('>实体</span>'))
  assert.match(apiSection, /智能体接口/)
  assert.match(apiSection, /通用聊天助手/)
  assert.match(apiSection, /POST \/agents\/assistant\/run/)
  assert.doesNotMatch(apiSection, /暂无接口/)
  assert.match(apiSection, /data-status="completed"/)
  assert.match(apiSection, />1\/1</)
  assert.deepEqual(developmentPlanningTotals([], [], artifacts, [assistant]), {
    total: 1,
    completed: 1
  })
})

test('智能体接口与普通接口、Agent 详情互斥选择，切换应用丢弃接口选择', () => {
  let view: ReturnType<typeof useDevelopmentArtifactDetail>
  /** 挂载真实选择 Hook，验证接口浏览不会产生普通字段映射目标。 */
  function Harness({ applicationId }: { applicationId: string }): null {
    view = useDevelopmentArtifactDetail({
      applicationId,
      setRightPanel: () => undefined,
      setRightPanelLayout: () => undefined
    })
    return null
  }
  let renderer: ReactTestRenderer
  act(() => {
    renderer = create(<Harness applicationId="one" />)
  })
  act(() => {
    view.artifactOutlineProps.onAgentInterfaceSelect?.(assistant)
  })
  assert.equal(view!.artifactOutlineProps.selectedAgentInterfaceId, 'assistant')
  assert.equal(view!.artifactOutlineProps.selectedAgentId, undefined)
  assert.equal(view!.apiTarget, undefined)
  act(() => {
    view.artifactOutlineProps.onAgentSelect?.(assistant)
  })
  assert.equal(view!.artifactOutlineProps.selectedAgentInterfaceId, undefined)
  assert.equal(view!.artifactOutlineProps.selectedAgentId, 'assistant')
  act(() => {
    view.artifactOutlineProps.onApiEndpointSelect({
      apiContractId: 'orders',
      endpointId: 'list',
      endpointKey: 'orders:list',
      label: '订单查询'
    })
  })
  assert.deepEqual(view!.apiTarget, { apiContractId: 'orders', endpointId: 'list' })
  assert.equal(view!.artifactOutlineProps.selectedAgentId, undefined)
  act(() => {
    view.artifactOutlineProps.onAgentInterfaceSelect?.(assistant)
  })
  act(() => {
    renderer.update(<Harness applicationId="two" />)
  })
  assert.equal(view!.artifactOutlineProps.selectedAgentInterfaceId, undefined)
  assert.equal(view!.apiTarget, undefined)
  act(() => {
    renderer.unmount()
  })
})

test('智能体接口支持名称路径协议搜索，点击保留 Agent 身份并提供只读调用详情', () => {
  for (const query of ['通用聊天助手', '/agents/assistant/run', 'AG-UI', 'sse', '智能体接口']) {
    assert.deepEqual(matchingAgentInterfaces([assistant], query), [assistant])
  }
  assert.deepEqual(matchingAgentInterfaces([assistant], '不存在'), [])
  let selected: BusinessAgentOption | undefined
  const row = AgentInterfaceRows({
    agents: [assistant],
    selectedAgentId: 'assistant',
    onSelect: (agent) => {
      selected = agent
    }
  })
  // 直接触发原生按钮，验证它使用 Agent 导航回调而不是普通 Endpoint 回调。
  row.props.children[0].props.onClick()
  assert.equal(selected, assistant)
  assert.match(renderToStaticMarkup(row), /aria-current="true"/)
  const html = renderToStaticMarkup(
    <AgentInterfaceDetail
      agent={assistant}
      progress={artifacts.agents.assistant}
      onOpenAgent={() => undefined}
    />
  )
  assert.match(html, /POST/)
  assert.match(html, /\/agents\/assistant\/run/)
  assert.match(html, /AG-UI \/ SSE/)
  assert.match(html, /查看智能体/)
  assert.doesNotMatch(html, /打开字段映射/)
})
