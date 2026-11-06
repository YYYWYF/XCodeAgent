import assert from 'node:assert/strict'
import { test } from 'node:test'
import { setTimeout } from 'node:timers/promises'
import { JSDOM } from 'jsdom'
import { createServer } from 'vite'
import { createRequire } from 'node:module'
import { createServer as createHttpServer } from 'node:http'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { cp, mkdtemp, mkdir, readFile, symlink, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { createConsumerMockServer } from '../../agent-ui/mock-consumer.mjs'

/** 等待真实组件状态收敛，超时保留页面文本帮助定位集成失败。 */
async function until(read, message) {
  for (let i = 0; i < 200; i++) {
    if (read()) return
    await setTimeout(40)
  }
  throw new Error(message + '\n' + document.body.textContent)
}

/** 为真实生成应用层提供测试 DOM 与官方 HTTP Mock，不替换 Agent 或解析 SSE。 */
async function mountConsumer(t, scenario = 'simple-chat', sharedProject) {
  const mock = sharedProject?.mockServer ?? createConsumerMockServer(scenario)
  await new Promise((resolve, reject) => {
    mock.once('error', reject)
    mock.listen(0, '127.0.0.1', resolve)
  })
  const origin = `http://127.0.0.1:${mock.address().port}`
  const dom = new JSDOM('<div id="root"></div>', { url: `${origin}/?scenario=${scenario}` })
  const names = ['window', 'document', 'navigator', 'HTMLElement', 'Element', 'Node', 'MutationObserver', 'getComputedStyle', 'Event', 'CustomEvent', 'MouseEvent', 'DOMRect', 'ShadowRoot', 'ResizeObserver', 'requestAnimationFrame', 'cancelAnimationFrame']
  const saved = new Map(names.map((name) => [name, Object.getOwnPropertyDescriptor(globalThis, name)]))
  for (const name of names) Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true, writable: true })
  // jsdom 不提供布局、媒体查询和动画帧；只补测试平台能力，不改包行为。
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} })
  globalThis.ResizeObserver = class {
    /** 测试 DOM 没有布局变化。 */
    observe() {}
    /** 测试 DOM 没有布局变化。 */
    unobserve() {}
    /** 测试 DOM 没有布局资源。 */
    disconnect() {}
  }
  globalThis.requestAnimationFrame = (callback) => globalThis.setTimeout(() => callback(Date.now()), 0)
  globalThis.cancelAnimationFrame = globalThis.clearTimeout
  const nativeFetch = globalThis.fetch
  // Node fetch 不接受浏览器相对 URL；仅补同源地址解析，原始响应仍由官方 SDK 消费。
  globalThis.fetch = (input, init) => nativeFetch(typeof input === 'string' ? new URL(input, origin) : input, init)
  const server = await createServer({
    configFile: false,
    root: sharedProject?.root,
    esbuild: { jsx: 'automatic' },
    server: { host: '127.0.0.1', middlewareMode: true, hmr: false, watch: null },
    ssr: { noExternal: [/^@za38\.03\//] },
  })
  // 目标拥有独立安装；测试 renderer 必须解析到该目标的同一份 React。
  const requireFromTarget = createRequire(sharedProject ? path.join(sharedProject.root, 'package.json') : import.meta.url)
  const { createElement } = await import(pathToFileURL(requireFromTarget.resolve('react')).href)
  const { createRoot } = await import(pathToFileURL(requireFromTarget.resolve('react-dom/client')).href)
  const module = await server.ssrLoadModule(sharedProject?.module ?? '/src/App.tsx')
  const App = sharedProject ? (module.SharedAgentApplication ?? module.SharedAgentHost ?? module.App) : module.App
  const { AgentUIProvider, useAgentUI } = await server.ssrLoadModule('@za38.03/agent-ui-runtime')
  let host
  /** 只在测试中观察公开 Host 状态，不向成品页面添加调试状态或控制按钮。 */
  function Observer() {
    host = useAgentUI()
    return null
  }
  const root = createRoot(document.getElementById('root'))
  const requests = []
  mock.on('request', (request) => { requests.push(request) })
  t.after(async () => {
    root.unmount()
    await server.close()
    mock.closeAllConnections()
    await new Promise((resolve) => mock.close(resolve))
    dom.window.close()
    globalThis.fetch = nativeFetch
    for (const [name, descriptor] of saved) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor)
      else delete globalThis[name]
    }
  })
  /** 外层 Provider 及 Host 的树位置稳定，测试只更新路由和显隐参数。 */
  function showPage(pageId = 'chat', panelOpen = true, sessionKey = 'user-a') {
    const props = { pageId, endpoint: '/agent', source: 'official-mock', panelOpen, sessionKey, agentId: sharedProject?.agentId,
      onNavigatePage: (target) => showPage(target, true, sessionKey),
      onPanelOpenChange: (open) => showPage(pageId, open, sessionKey),
      children: createElement('main', null, '业务首页', sessionKey && createElement(Observer),
        module.SharedAgentPage && pageId === 'chat' && createElement(module.SharedAgentPage, { pageId })),
    }
    root.render(module.SharedAgentApplication
      ? createElement(App, props)
      : createElement(AgentUIProvider, null, createElement(App, sharedProject ? props : {}), createElement(Observer)))
  }
  showPage()
  await until(() => host?.getSnapshot().status === 'ready', 'Agent did not become ready')
  // 初次 ready 后 upstream 仍会提交初始 thread-list 选择；等待 React 初始提交结束再驱动用户动作。
  await setTimeout(200)
  return { requests, mock, snapshot: () => host.getSnapshot().runtime, showPage, getHost: () => host }
}

/** 从本仓库的真实应用层准备 Host 回归；独立成品转入另由集成用例验证。 */
async function mountSharedConsumer(t, scenario, agentId, mockServer, preview = false) {
  const seed = path.resolve(import.meta.dirname, '..')
  const root = await mkdtemp(path.join(tmpdir(), 'agent-ui-host-'))
  t.after(() => rm(root, { recursive: true, force: true }))
  await cp(path.join(seed, 'src/agent-ui'), path.join(root, 'src/agent-ui'), { recursive: true })
  await symlink(path.join(seed, 'node_modules'), path.join(root, 'node_modules'), 'dir')
  await writeFile(path.join(root, 'package.json'), await readFile(path.join(seed, 'package.json')))
  await mkdir(path.join(root, 'src/design'))
  for (const name of ['Agent.tsx', 'SharedHost.tsx', 'SharedApplication.tsx', 'SharedPage.tsx',
    'runtimeResults.ts', 'useRuntimeResult.ts', 'shared-host.css']) {
    await cp(path.resolve(seed, '../agent-ui-design', name), path.join(root, 'src/design', name))
  }
  await writeFile(path.join(root, 'src/design/props.ts'), 'export const agentProps = {}')
  await writeFile(path.join(root, 'src/design/surfaces.ts'), `export const agentSurfaces = [
    {pageId:'chat',surface:'standalone_page',entries:[]},
    {pageId:'home',surface:'floating_panel',entries:[{actionId:'open',label:'打开助手'}]}] as const`)
  await writeFile(path.join(root, 'src/design/presentation.ts'), `export const pagePresentation: Record<string, {props: object; css: string}> = {
    chat:{props:{welcome:{title:'独立页欢迎'}},css:''},home:{props:{welcome:{title:'首页欢迎'}},css:''}}`)
  if (preview) {
    await cp(path.resolve(seed, '../agent-ui-design/App.tsx'), path.join(root, 'src/App.tsx'))
    await cp(path.resolve(seed, '../agent-ui-design/host.css'), path.join(root, 'src/design/host.css'))
    await writeFile(path.join(root, 'src/design/locales.json'), JSON.stringify({
      mockSource: '官方模拟服务', source: '设计预览', conversation: '智能体会话', close: '关闭助手',
    }))
    await writeFile(path.join(root, 'src/design/pages.tsx'), `
      /** 只渲染已确认导航，不复制官方消息区。 */
      function Chat({onNavigatePage}) {return <button data-action-id="back_home" onClick={() => onNavigatePage('home')}>返回首页</button>}
      /** 浮窗业务正文与已确认跳转。 */
      function Home({onNavigatePage}) {return <button data-action-id="go_chat" onClick={() => onNavigatePage('chat')}>进入智能体页</button>}
      export const designPages = [
        {pageId:'chat',surface:'standalone_page',component:Chat,entries:[]},
        {pageId:'home',surface:'floating_panel',component:Home,entries:[{actionId:'open',label:'打开助手'}]},
      ]`)
  }
  return mountConsumer(t, scenario, { root,
    module: preview ? '/src/App.tsx' : '/src/design/SharedApplication.tsx', agentId, mockServer })
}

/** 独立页导航和官方会话同时可见，来回切页仍保留唯一 Composer 和当前线程。 */
test('design preview retains standalone navigation beside one official conversation', async (t) => {
  const { snapshot, requests } = await mountSharedConsumer(t, 'simple-chat', undefined, undefined, true)
  const thread = snapshot().conversation.id
  const composer = document.querySelector('textarea')
  assert.ok(document.querySelector('[data-action-id="back_home"]'))
  assert.equal(document.querySelectorAll('textarea').length, 1)
  document.querySelector('[data-action-id="back_home"]').click()
  await until(() => document.querySelector('[data-design-page="home"]'), 'Navigation did not reach home')
  document.querySelector('[data-action-id="go_chat"]').click()
  await until(() => document.querySelector('[data-design-page="chat"]'), 'Navigation did not return to chat')
  assert.equal(document.querySelector('textarea'), composer)
  assert.equal(snapshot().conversation.id, thread)
  assert.equal(requests.length, 0)
})

/** 仅测试的当前 Python 事件夹具；不调用模型，也不冒充实际生成的 Runtime 验收。 */
function runtimeResultFixture(inputs = []) {
  return createHttpServer(async (request, response) => {
    let body = ''
    for await (const chunk of request) body += chunk
    const input = JSON.parse(body)
    inputs.push(input)
    const { threadId, runId } = input
    const value = { schemaVersion: 1, agentId: 'assistant', threadId, runId, status: 'completed', pendingInteraction: null, result: null }
    response.writeHead(200, { 'Content-Type': 'text/event-stream' })
    response.write(`data: ${JSON.stringify({ type: 'RUN_STARTED', threadId, runId })}\n\n`)
    await setTimeout(60)
    for (const event of [
      { type: 'TEXT_MESSAGE_START', messageId: runId, role: 'assistant' },
      { type: 'TEXT_MESSAGE_CONTENT', messageId: runId, delta: '合同夹具回复' },
      { type: 'TEXT_MESSAGE_END', messageId: runId },
      { type: 'CUSTOM', name: 'agent_runtime_result.v1', value },
      { type: 'STATE_SNAPSHOT', snapshot: { agentRuntime: value } },
      { type: 'RUN_FINISHED', threadId, runId, result: { agentRuntime: value } },
    ]) response.write(`data: ${JSON.stringify(event)}\n\n`)
    response.end()
  })
}

/** 官方客户端第二轮回传公开状态镜像；Python 必须校验后忽略，不能当业务输入。 */
test('plain text requests preserve the Python request boundary across two turns', async (t) => {
  const inputs = []
  const { getHost, snapshot } = await mountSharedConsumer(t, 'runtime-text', 'assistant', runtimeResultFixture(inputs))
  await getHost().sendMessage('记住测试词：青竹')
  await until(() => inputs.length === 1 && snapshot().run.status === 'idle', 'First turn did not settle')
  await getHost().sendMessage('测试词是什么？')
  await until(() => inputs.length === 2 && snapshot().run.status === 'idle', 'Second turn did not settle')
  assert.deepEqual(inputs[0].state ?? {}, {})
  assert.equal(inputs[1].state.agentRuntime.threadId, inputs[0].threadId)
  assert.equal(inputs[1].state.agentRuntime.runId, inputs[0].runId)
  assert.equal(inputs[1].state.agentRuntime.result, null)
  for (const input of inputs) {
    assert.equal(input.protocolVersion, '1.0')
    assert.deepEqual(Object.keys(input).filter(key => !['threadId', 'runId', 'tools', 'context', 'forwardedProps', 'state', 'messages', 'protocolVersion'].includes(key)), [])
    assert.deepEqual(input.context ?? [], [])
    assert.deepEqual(input.tools ?? [], [])
    assert.ok(input.messages.every(message => Object.keys(message).sort().join(',') === 'content,id,role'))
  }
  assert.equal(inputs[0].threadId, inputs[1].threadId)
})

/** 即使收到当前请求的合法 Python 结果，包未公开 runId 时仍不能独立核验。 */
test('well-formed Python HTTP result stays unknown with the actual package snapshot', async (t) => {
  const { getHost, snapshot, requests } = await mountSharedConsumer(t, 'runtime-result', 'assistant', runtimeResultFixture())
  await getHost().sendMessage('仅合同夹具')
  await until(() => document.querySelector('[data-runtime-business-status="unknown"]'), 'Unverified result was treated as success')
  assert.equal(snapshot().run.id, undefined)
  assert.equal(requests.length, 1)
  assert.equal(document.querySelector('[data-runtime-business-status="completed"]'), null)
  assert.match(document.body.textContent, /合同夹具回复/)
})

/** 实际安装包没有公开 run.id；官方 Mock 的 wire 结束也不能成为 X 的业务成功。 */
test('shared Host reports unknown without public run identity and clears it on user change', async (t) => {
  const { getHost, snapshot, showPage, requests } = await mountSharedConsumer(t, 'simple-chat', 'assistant')
  await getHost().sendMessage('本轮结果观察')
  await until(() => document.querySelector('[data-runtime-business-status="unknown"]'), 'Missing unknown business state')
  assert.equal(snapshot().run.id, undefined)
  assert.match(document.body.textContent, /未提供可核验的运行身份/)
  assert.equal(requests.length, 1)
  showPage('home', true)
  await until(() => document.getElementById('shared-agent-conversation')?.classList.contains('is-floating'), 'Page switch did not settle')
  assert.ok(document.querySelector('[data-runtime-business-status="unknown"]'))
  showPage('chat', true, 'user-b')
  await until(() => !document.querySelector('[data-runtime-business-status]'), 'Previous result leaked across users')
  assert.equal(requests.length, 1)
})

/** 页面展示变化不应卸载 Composer，用户切换则必须清理整棵会话树。 */
test('shared application preserves draft across routes and resets it on identity change', async (t) => {
  const { showPage, snapshot, requests } = await mountSharedConsumer(t)
  showPage('home', true)
  await until(() => document.querySelector('textarea'), 'Composer missing')
  const composer = document.querySelector('textarea')
  const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set
  setter.call(composer, '尚未发送的草稿')
  composer.dispatchEvent(new window.Event('input', { bubbles: true }))
  await setTimeout(50)
  const thread = snapshot().conversation.id
  showPage('chat', true)
  await until(() => document.body.textContent.includes('独立页欢迎'), 'Standalone presentation missing')
  assert.equal(document.querySelector('textarea'), composer)
  assert.equal(composer.value, '尚未发送的草稿')
  showPage('home', false)
  await until(() => document.getElementById('shared-agent-conversation').hidden, 'Panel not hidden')
  showPage('home', true)
  await until(() => !document.getElementById('shared-agent-conversation').hidden, 'Panel not reopened')
  assert.equal(document.querySelector('textarea'), composer)
  assert.equal(snapshot().conversation.id, thread)
  showPage('home', true, 'user-b')
  await until(() => snapshot()?.conversation.id !== thread && document.querySelector('textarea') !== composer,
    'Identity change reused the previous session')
  assert.equal(document.querySelector('textarea').value, '')
  assert.equal(requests.length, 0)
})

/** 身份变化清理运行 owner；迟到事件和旧 Host 方法不能写入下一用户。 */
test('switching identity ends the old Host and does not replay an active run', async (t) => {
  const { showPage, snapshot, requests, getHost } = await mountSharedConsumer(t, 'cancel-before-first-output')
  const oldHost = getHost()
  const thread = snapshot().conversation.id
  void oldHost.sendMessage('本轮仅发送一次').catch(() => undefined)
  await until(() => requests.length === 1 && snapshot().run.status === 'running', 'Run did not start')
  showPage('home', true, 'user-b')
  await until(() => snapshot()?.conversation.id !== thread && snapshot()?.run.status === 'idle', 'Old run leaked into next identity')
  await assert.rejects(oldHost.sendMessage('旧身份不能再发送'), (error) =>
    ['AGENT_UI_SESSION_ENDED', 'AGENT_UI_NOT_READY'].includes(error.code))
  assert.equal(snapshot().messages.length, 0)
  assert.equal(requests.length, 1)
  showPage('chat', true, null)
  await until(() => !document.getElementById('shared-agent-conversation'), 'Logout retained the old conversation')
  assert.match(document.body.textContent, /业务首页/)
  assert.ok(document.querySelector('.shared-agent-page-slot'), 'Logout broke the standalone page')
})

/** 全页只移动常驻容器到页面内容区，Shell 导航和会话 DOM 均不被重建。 */
test('standalone placement preserves the Shell and the same conversation element', async (t) => {
  const { showPage, snapshot } = await mountSharedConsumer(t)
  showPage('home', true)
  await until(() => document.querySelector('textarea'), 'Composer missing')
  const section = document.getElementById('shared-agent-conversation')
  const thread = snapshot().conversation.id
  showPage('chat', true)
  await until(() => document.querySelector('.shared-agent-page-slot'), 'Page slot missing')
  const slot = document.querySelector('.shared-agent-page-slot')
  slot.getBoundingClientRect = () => ({ top: 90, left: 200, width: 640, height: 480 })
  window.dispatchEvent(new window.Event('resize'))
  await until(() => section.style.width === '640px', 'Host did not use the actual content bounds')
  assert.equal(section.hidden, false)
  assert.equal(section.style.left, '200px')
  assert.equal(document.getElementById('shared-agent-conversation'), section)
  assert.equal(snapshot().conversation.id, thread)
  assert.equal(document.querySelector('main').parentElement.hidden, false)
})

/** 通过真实宿主按钮驱动组件。 */
function click(label) {
  const button = [...document.querySelectorAll('button')].find((item) => item.textContent.includes(label) || item.getAttribute('aria-label') === label)
  assert.ok(button, `Missing button: ${label}`)
  assert.equal(button.disabled, false, label)
  button.click()
}

/** 官方建议与正常发送/运行状态保留，页面不包含人为主题、状态或呈现切换控件。 */
test('official defaults and real conversation work without demonstration controls', async (t) => {
  const { requests, snapshot } = await mountConsumer(t)
  for (const label of ['账户交易查询', '转账汇款指引', '银行卡服务']) assert.ok(document.body.textContent.includes(label))
  assert.doesNotMatch(document.body.textContent, /切换明暗主题|发送测试消息|当前场景|消息 0/)
  assert.equal(document.querySelector('nav[aria-label="会话呈现方式"]'), null)
  const id = snapshot().conversation.id
  click('账户交易查询')
  await until(() => snapshot().run.status === 'running', 'Run did not start')
  await until(() => snapshot().run.status === 'idle' && snapshot().messages.length === 2, 'Text run did not settle')
  assert.match(document.body.textContent, /你好，这是一个纯文本流式回复/)
  assert.equal(snapshot().conversation.id, id)
  assert.equal(requests.length, 1)
})

/** 首字前停止必须释放发送占用，且不会出现自动补发请求。 */
test('stop before first output settles without replay', async (t) => {
  const { requests, snapshot } = await mountConsumer(t, 'cancel-before-first-output')
  click('账户交易查询')
  await until(() => snapshot().run.status === 'running' && requests.length === 1, 'Run did not start')
  click('停止生成')
  await until(() => snapshot().run.status === 'idle', 'Cancellation did not settle')
  await setTimeout(200)
  assert.equal(requests.length, 1)
})

/** 中途断开真实 HTTP 连接，必须报告错误而非隐式重放原请求。 */
test('broken stream reports error without replay', async (t) => {
  const { requests, mock, snapshot } = await mountConsumer(t, 'cancel-before-first-output')
  click('账户交易查询')
  await until(() => snapshot().run.status === 'running' && requests.length === 1, 'Run did not start')
  mock.closeAllConnections()
  await until(() => snapshot().run.status === 'error' || document.querySelector('[role="alert"]'), 'Dropped stream did not report failure')
  await setTimeout(200)
  assert.equal(requests.length, 1)
})

/** 使用已经实际转入的共享应用，切页和关闭浮窗不创建第二个运行或丢失会话。 */
test('transferred shared Host keeps one session across page props and hidden surfaces', {
  skip: !process.env.AGENT_UI_SHARED_PROJECT,
}, async (t) => {
  const { requests, snapshot, showPage } = await mountConsumer(t, 'cancel-before-first-output', {
    root: process.env.AGENT_UI_SHARED_PROJECT,
    module: process.env.AGENT_UI_SHARED_HOST_MODULE,
  })
  const conversation = snapshot().conversation.id
  assert.match(document.body.textContent, /独立页欢迎/)
  assert.equal(document.querySelectorAll('.agent-ui-platform-shell').length, 1)
  showPage('home', true)
  await until(() => document.body.textContent.includes('首页欢迎'), 'Page-specific welcome did not change')
  assert.equal(snapshot().conversation.id, conversation)
  click('关闭助手')
  await until(() => document.getElementById('shared-agent-conversation').hidden, 'Closing the panel unmounted the host')
  assert.equal(snapshot().conversation.id, conversation)
  click('打开助手')
  await until(() => !document.getElementById('shared-agent-conversation').hidden, 'Launcher did not reopen the host')
  // 通过输入框发送，不依赖目标设计是否保留某条示例建议。
  const composer = document.querySelector('textarea')
  assert.ok(composer, 'Transferred Composer missing')
  const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set
  setter.call(composer, '共享交接验收')
  composer.dispatchEvent(new window.Event('input', { bubbles: true }))
  await until(() => document.querySelector('button[aria-label="发送消息"]')?.disabled === false,
    'Transferred Composer did not enable sending')
  click('发送消息')
  await until(() => snapshot().run.status === 'running' && requests.length === 1, 'Shared run did not start')
  showPage('chat', true)
  await until(() => document.getElementById('shared-agent-conversation').classList.contains('is-standalone'), 'Standalone page did not render')
  assert.equal(snapshot().conversation.id, conversation)
  assert.equal(snapshot().run.status, 'running')
  assert.equal(document.querySelectorAll('.agent-ui-platform-shell').length, 1)
  click('停止生成')
  await until(() => snapshot().run.status === 'idle', 'Shared run did not stop')
  assert.equal(requests.length, 1)
})
