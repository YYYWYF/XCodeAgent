import assert from 'node:assert/strict'
import { test } from 'node:test'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { useAgentSurfaceDraft } from '../src/renderer/src/components/AiChatPanel/components/DocPanel/useAgentSurfaceDraft'

type SelectionInput = {
  action: 'get' | 'save'
  workspaceRoot: string
  agentId?: string
  pageId?: string
  enabled?: boolean
  basedOnProductPlanHash?: string
}

type Server = { version: number; selected: boolean; calls: SelectionInput[] }
type DraftView = ReturnType<typeof useAgentSurfaceDraft>

/** 构造同一份规划的开关状态，模拟 Workflow 内存快照落后于磁盘草稿。 */
function productPlan(selected: boolean): Record<string, unknown> {
  return {
    confirmation_status: 'pending_user_confirmation',
    pages: [{ pageId: 'home', name: '首页' }],
    agents: [
      {
        agentId: 'assistant',
        name: '助手',
        pageActionBindings: [
          { pageId: 'home', surface: { type: 'floating_panel', enabled: selected } }
        ]
      }
    ]
  }
}

/** 从当前视图读取保存回执回填的开关值。 */
function selected(view: DraftView): boolean {
  const agents = view.productPlan.agents as Array<{
    pageActionBindings: Array<{ surface: { enabled: boolean } }>
  }>
  return agents[0].pageActionBindings[0].surface.enabled
}

/** 用服务端版本构造不依赖客户端序列化的摘要。 */
function digest(server: Server): string {
  return `sha256:${String(server.version).padStart(64, '0')}`
}

/** 返回标准 AG-UI 事件，让真实 HttpAgent 消费读取、保存和业务失败。 */
function streamResponse(threadId: string, runId: string, data: Record<string, unknown>): Response {
  const messageId = 'surface-result'
  const value = { schemaVersion: 1, threadId, runId, ...data }
  const events = [
    { type: 'RUN_STARTED', threadId, runId },
    { type: 'TEXT_MESSAGE_START', messageId, role: 'assistant' },
    { type: 'TEXT_MESSAGE_CONTENT', messageId, delta: '浮窗草稿操作完成' },
    { type: 'CUSTOM', name: 'agent-surface-selection-draft', value },
    { type: 'STATE_SNAPSHOT', snapshot: { agentSurfaceSelection: value } },
    { type: 'TEXT_MESSAGE_END', messageId },
    { type: 'RUN_FINISHED', threadId, runId }
  ]
  return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), {
    headers: { 'Content-Type': 'text/event-stream' }
  })
}

/** 隔离网络和工作区环境，并在用例完成后恢复全局对象。 */
async function withServer(operation: (server: Server) => Promise<void>): Promise<void> {
  const originalWindow = globalThis.window
  const originalFetch = globalThis.fetch
  const server: Server = { version: 1, selected: true, calls: [] }
  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: { devAgentStudio: { agentBaseUrl: 'http://agent.test' } }
  })
  globalThis.fetch = async (_url, init) => {
    const body = JSON.parse(String(init?.body)) as {
      threadId: string
      runId: string
      forwardedProps: { agentSurfaceSelection: SelectionInput }
    }
    const input = body.forwardedProps.agentSurfaceSelection
    server.calls.push(input)
    if (input.action === 'save') {
      if (input.basedOnProductPlanHash !== digest(server)) {
        return streamResponse(body.threadId, body.runId, {
          status: 'failed',
          error: { message: '产品草稿已变化，请重新加载后再修改浮窗选择。' }
        })
      }
      server.selected = input.enabled === true
      server.version += 1
    }
    return streamResponse(body.threadId, body.runId, {
      status: 'completed',
      productPlan: productPlan(server.selected),
      productPlanHash: digest(server)
    })
  }
  try {
    await operation(server)
  } finally {
    globalThis.fetch = originalFetch
    Object.defineProperty(globalThis, 'window', { configurable: true, value: originalWindow })
  }
}

test('浮窗先读取磁盘草稿，连续关闭开启使用每次保存返回的新摘要', async () => {
  await withServer(async (server) => {
    const stalePlan = productPlan(false)
    let view: DraftView | undefined
    let refreshed = 0
    let renderer: ReactTestRenderer | undefined
    /** 捕获真实 Hook 的当前渲染输出，保持上游 Workflow 快照不变。 */
    function Probe(): null {
      view = useAgentSurfaceDraft(stalePlan, '/workspace', () => {
        refreshed += 1
      })
      return null
    }
    try {
      await act(async () => {
        renderer = create(<Probe />)
      })
      assert.equal(view!.editable, true)
      assert.equal(selected(view!), true)
      const firstDigest = digest(server)
      await act(async () => {
        await view!.toggle('assistant', 'home', false)
      })
      assert.equal(selected(view!), false)
      assert.equal(server.calls[1].basedOnProductPlanHash, firstDigest)
      const secondDigest = digest(server)
      await act(async () => {
        await view!.toggle('assistant', 'home', true)
      })
      assert.equal(selected(view!), true)
      assert.equal(server.calls[2].basedOnProductPlanHash, secondDigest)
      assert.notEqual(firstDigest, secondDigest)
      assert.equal(refreshed, 2)
      assert.equal(view!.productPlan.confirmation_status, 'pending_user_confirmation')
      assert.deepEqual(
        server.calls.map((input) => input.action),
        ['get', 'save', 'save']
      )
    } finally {
      act(() => renderer?.unmount())
    }
  })
})

test('真正的草稿冲突不自动重放写入，用户重新加载后可再次保存', async () => {
  await withServer(async (server) => {
    const initial = productPlan(true)
    let view: DraftView | undefined
    let renderer: ReactTestRenderer | undefined
    /** 捕获冲突后的界面状态和显式重读入口。 */
    function Probe(): null {
      view = useAgentSurfaceDraft(initial, '/workspace')
      return null
    }
    try {
      await act(async () => {
        renderer = create(<Probe />)
      })
      server.version += 1
      await act(async () => {
        await view!.toggle('assistant', 'home', false)
      })
      assert.match(view!.error, /产品草稿已变化/)
      assert.equal(selected(view!), true)
      assert.deepEqual(
        server.calls.map((input) => input.action),
        ['get', 'save']
      )
      await act(async () => {
        view!.reload()
      })
      assert.equal(view!.error, '')
      await act(async () => {
        await view!.toggle('assistant', 'home', false)
      })
      assert.equal(selected(view!), false)
      assert.deepEqual(
        server.calls.map((input) => input.action),
        ['get', 'save', 'get', 'save']
      )
    } finally {
      act(() => renderer?.unmount())
    }
  })
})
