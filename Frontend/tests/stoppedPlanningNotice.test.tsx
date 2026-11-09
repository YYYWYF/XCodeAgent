import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { Button } from 'antd'
import StoppedPlanningNotice from '../src/renderer/src/components/AiChatPanel/components/StoppedPlanningNotice'
import type { ApplicationConfig, ApplicationLifecycle } from '../src/renderer/src/typings'

/** 返回真实客户端消费的标准生命周期事件；仅控制读取结果，不执行任何生成动作。 */
function lifecycleResponse(request: Record<string, unknown>, lifecycle?: ApplicationLifecycle): Response {
  const payload = { schemaVersion: 1, runId: request.runId, threadId: request.threadId,
    status: lifecycle ? 'completed' : 'failed', lifecycle,
    ...(!lifecycle ? { error: { type: 'ReadError', message: '读取暂时失败' } } : {}) }
  const frames = [
    { type: 'RUN_STARTED', runId: request.runId, threadId: request.threadId },
    { type: 'CUSTOM', name: 'application-lifecycle', value: payload },
    { type: 'STATE_SNAPSHOT', snapshot: { applicationLifecycle: payload } },
    { type: 'RUN_FINISHED', runId: request.runId, threadId: request.threadId }
  ]
  return new Response(frames.map((frame) => `data: ${JSON.stringify(frame)}\n\n`).join(''),
    { headers: { 'content-type': 'text/event-stream' } })
}

test('停止卡检查失败可原位重试，重试中禁用重复点击，检查成功仍须显式继续', async () => {
  const savedWindow = Object.getOwnPropertyDescriptor(globalThis, 'window')
  const savedDocument = Object.getOwnPropertyDescriptor(globalThis, 'document')
  const savedFetch = globalThis.fetch
  // react-test-renderer 不创建 DOM 节点；Ant Design 仍会用这些宿主构造器做类型判断。
  const constructors = { Element: class {}, HTMLElement: class {}, SVGElement: class {}, ShadowRoot: class {} }
  const savedConstructors = new Map<string, PropertyDescriptor | undefined>()
  for (const [name, constructor] of Object.entries(constructors)) {
    savedConstructors.set(name, Object.getOwnPropertyDescriptor(globalThis, name))
    Object.defineProperty(globalThis, name, { configurable: true, value: constructor })
  }
  const events = new EventTarget()
  type StyleNode = { parentNode: unknown; innerHTML: string; tagName: string;
    setAttribute: (name: string, value: string) => void; getAttribute: (name: string) => string | null }
  const head = {
    children: [] as StyleNode[],
    /** 只承载 Ant Design 图标动态注入的样式，不模拟产品交互。 */
    appendChild(node: StyleNode): StyleNode { this.children.push(node); node.parentNode = this; return node },
    /** 保留样式容器的插入顺序。 */
    insertBefore(node: StyleNode, before?: StyleNode): StyleNode {
      const index = before ? this.children.indexOf(before) : -1
      this.children.splice(index < 0 ? this.children.length : index, 0, node); node.parentNode = this; return node
    },
    /** 移除样式库自身创建的临时定位节点。 */
    removeChild(node: StyleNode): void {
      const index = this.children.indexOf(node)
      if (index >= 0) this.children.splice(index, 1)
      node.parentNode = null
    }
  }
  const requests: Array<{ request: Record<string, unknown>; resolve: (value: Response) => void }> = []
  const application = { id: 'stop-retry', workspaceRoot: '/tmp/stop-retry', appName: '停止重读' } as ApplicationConfig
  const lifecycle: ApplicationLifecycle = {
    application: { id: application.id, name: application.appName }, revision: 1, updatedAt: '',
    initialization: { stage: 'generating_technical_plan', status: 'cancelled', threadId: 'planning' },
    activeExecutions: {}, endedPlanningExecution: {
      runId: 'stopped', threadId: 'planning', stage: 'generating_technical_plan',
      workspaceUnchanged: true, canRestart: true
    }
  }
  Object.defineProperty(globalThis, 'window', { configurable: true, value: {
    devAgentStudio: { agentBaseUrl: 'http://agent.test' },
    setTimeout, clearTimeout,
    addEventListener: events.addEventListener.bind(events), removeEventListener: events.removeEventListener.bind(events)
  } })
  Object.defineProperty(globalThis, 'document', { configurable: true, value: {
    hidden: false, head, body: head,
    querySelector: () => head,
    contains: (node: unknown) => node === head || head.children.includes(node as StyleNode),
    /** 提供图标样式节点的属性去重能力。 */
    createElement: (tag: string): StyleNode => {
      const attributes = new Map<string, string>()
      return { tagName: tag.toUpperCase(), parentNode: null, innerHTML: '',
        setAttribute: (name, value) => { attributes.set(name, value) },
        getAttribute: (name) => attributes.get(name) || null }
    },
    addEventListener: events.addEventListener.bind(events), removeEventListener: events.removeEventListener.bind(events)
  } })
  Object.defineProperty(globalThis.window, 'document', { value: globalThis.document })
  globalThis.fetch = async (_url, init) => new Promise<Response>((resolve) => {
    requests.push({ request: JSON.parse(String(init?.body)), resolve })
  })
  let renderer: ReactTestRenderer | undefined
  let continues = 0, restarts = 0
  const published: ApplicationLifecycle[] = []
  /** 卡片新增详情和诊断复制后，仍按业务动作名称定位唯一资格重读入口。 */
  const retryButton = () => renderer!.root.findAllByType(Button).find((button) => button.props.children === '重新检查')!
  try {
    await act(async () => { renderer = create(createElement(StoppedPlanningNotice, {
      application, stopped: lifecycle.endedPlanningExecution!, disabled: false,
      onLifecycle: (value) => { published.push(value) },
      onContinue: () => { continues++ }, onRestart: () => { restarts++ }
    })) })
    assert.equal(requests.length, 1)
    await act(async () => { requests[0].resolve(lifecycleResponse(requests[0].request)) })
    assert.match(JSON.stringify(renderer!.toJSON()), /工作区状态检查失败/)
    let retry = retryButton()
    assert.equal(retry.props.children, '重新检查')
    assert.equal(retry.props.disabled, false)
    assert.equal(renderer!.root.findByType('section').props['aria-busy'], false)

    await act(async () => { retry.props.onClick() })
    assert.equal(requests.length, 2)
    retry = retryButton()
    assert.equal(retry.props.disabled, true)
    assert.equal(retry.props.loading, true)
    await act(async () => { retry.props.onClick() })
    assert.equal(requests.length, 2, '读取中不能重复提交')
    await act(async () => { requests[1].resolve(lifecycleResponse(requests[1].request, lifecycle)) })
    assert.equal(requests.length, 3)
    // Attach 成功但 Get 失败也必须保留重试，不把半成功当成恢复授权。
    await act(async () => { requests[2].resolve(lifecycleResponse(requests[2].request)) })
    assert.equal(retryButton().props.children, '重新检查')
    assert.equal(published.length, 0)
    await act(async () => { retryButton().props.onClick() })
    await act(async () => { requests[3].resolve(lifecycleResponse(requests[3].request, lifecycle)) })
    await act(async () => { requests[4].resolve(lifecycleResponse(requests[4].request, lifecycle)) })
    assert.match(JSON.stringify(renderer!.toJSON()), /继续生成/)
    assert.doesNotMatch(JSON.stringify(renderer!.toJSON()), /工作区状态检查失败|重新检查/)
    assert.equal(published.length, 1)
    assert.equal(continues, 0, '检查成功不能自动继续任务')
    assert.equal(restarts, 0)
    assert.ok(requests.every(({ request }) => ['workspace_attach', 'get'].includes(
      (request.forwardedProps as { applicationLifecycle: { action: string } }).applicationLifecycle.action)))
  } finally {
    if (renderer) await act(async () => { renderer!.unmount() })
    requests.forEach(({ request, resolve }) => resolve(lifecycleResponse(request)))
    globalThis.fetch = savedFetch
    if (savedWindow) Object.defineProperty(globalThis, 'window', savedWindow)
    else Reflect.deleteProperty(globalThis, 'window')
    if (savedDocument) Object.defineProperty(globalThis, 'document', savedDocument)
    else Reflect.deleteProperty(globalThis, 'document')
    for (const [name, descriptor] of savedConstructors) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor)
      else Reflect.deleteProperty(globalThis, name)
    }
  }
})
