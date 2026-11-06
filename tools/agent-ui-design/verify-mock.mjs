import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { setTimeout } from 'node:timers/promises'
import { test } from 'node:test'

const [project, url, mode = 'simple-chat'] = process.argv.slice(2)
const target = resolve(project)
const requireTarget = createRequire(resolve(target, 'package.json'))

/** 所有 UI 依赖都解析自本次设计工程，工具侧不引入第二套 React。 */
async function dependency(name) {
  return import(pathToFileURL(requireTarget.resolve(name)).href)
}

/** 等待官方 Runtime 与实际 HTTP 事件完成提交，失败时输出可见文本便于复核。 */
async function until(read, message) {
  for (let index = 0; index < 200; index++) {
    if (read()) return
    await setTimeout(40)
  }
  throw new Error(`${message}\n${document.body.textContent}`)
}

/** 测试只操作用户正常使用的官方建议、停止和浮窗按钮。 */
function click(label) {
  const button = [...document.querySelectorAll('button')].find((item) =>
    item.textContent.includes(label) || item.getAttribute('aria-label') === label)
  assert.ok(button && !button.disabled, `Missing enabled control: ${label}`)
  button.click()
}

test(`actual design snapshot and official Mock: ${mode}`, async (t) => {
  const { JSDOM } = await dependency('jsdom')
  const nativeFetch = globalThis.fetch
  const html = await (await nativeFetch(url)).text()
  const dom = new JSDOM(html, { url })
  const config = JSON.parse(dom.window.document.querySelector('meta[name="agent-ui-design-preview"]').content)
  assert.equal(config.source, 'official-mock')
  assert.equal(new URL(config.endpoint, url).origin, new URL(url).origin)
  const names = ['window', 'document', 'navigator', 'HTMLElement', 'Element', 'Node', 'MutationObserver',
    'getComputedStyle', 'Event', 'CustomEvent', 'MouseEvent', 'DOMRect', 'ShadowRoot',
    'ResizeObserver', 'requestAnimationFrame', 'cancelAnimationFrame', 'fetch']
  const saved = new Map(names.map((name) => [name, Object.getOwnPropertyDescriptor(globalThis, name)]))
  for (const name of names.slice(0, 13)) {
    Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true })
  }
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} })
  globalThis.ResizeObserver = class {
    /** jsdom 无布局观察。 */
    observe() {}
    /** jsdom 无布局订阅。 */
    unobserve() {}
    /** jsdom 无布局资源。 */
    disconnect() {}
  }
  globalThis.requestAnimationFrame = (callback) => globalThis.setTimeout(() => callback(Date.now()), 0)
  globalThis.cancelAnimationFrame = globalThis.clearTimeout
  const requests = []
  // 仅适配 Node 的相对地址；完整响应仍由包内 canonical Runtime 和官方 SDK 消费。
  globalThis.fetch = (input, init) => {
    requests.push(typeof input === 'string' ? input : input.url)
    return nativeFetch(typeof input === 'string' ? new URL(input, url) : input, init)
  }
  const { createServer } = await dependency('vite')
  const server = await createServer({ root: target, configFile: false,
    server: { middlewareMode: true, hmr: false, watch: null }, ssr: { noExternal: [/^@za38\.03\//] } })
  const { createElement } = await dependency('react')
  const { createRoot } = await dependency('react-dom/client')
  const { App } = await server.ssrLoadModule('/src/App.tsx')
  const { AgentUIProvider, useAgentUI } = await server.ssrLoadModule('@za38.03/agent-ui-runtime')
  let host
  /** 公开 Host 仅供测试断言，不在页面显示 thread、run 或调试工具栏。 */
  function Observer() { host = useAgentUI(); return null }
  const root = createRoot(document.getElementById('root'))
  /** 正常页面变更保留同一 Provider 和 Agent 树位置。 */
  function render(props) {
    root.render(createElement(AgentUIProvider, null, createElement(App, props), createElement(Observer)))
  }
  t.after(async () => {
    root.unmount()
    await server.close()
    dom.window.close()
    for (const [name, descriptor] of saved) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor)
      else delete globalThis[name]
    }
  })
  const chatConfig = { ...config, pageId: 'chat' }
  render(chatConfig)
  await until(() => host?.getSnapshot().status === 'ready', 'Agent did not become ready')
  await setTimeout(200)
  const thread = host.getSnapshot().runtime.conversation.id
  assert.match(document.body.textContent, /官方模拟服务.*未连接真实业务/)
  assert.doesNotMatch(document.body.textContent, /发送测试消息|当前场景|切换明暗主题/)
  click('账户交易查询')
  await until(() => host.getSnapshot().runtime.run.status === 'running', 'Run did not start')
  if (mode === 'cancel-before-first-output') {
    // 官方 Runtime 可先建立空 assistant 占位；首字前停止要求尚无回复正文。
    for (const message of host.getSnapshot().runtime.messages.filter(({ role }) => role === 'assistant')) {
      assert.ok(!message.content || message.content.length === 0, 'Assistant output arrived before Stop')
    }
    click('停止生成')
    await until(() => host.getSnapshot().runtime.run.status === 'idle', 'Stop did not settle')
  } else if (mode === 'tool-error' || mode === 'disconnect') {
    if (mode === 'disconnect') process.stdout.write('DROP_STREAM\n')
    await until(() => host.getSnapshot().runtime.run.status === 'error' || document.querySelector('[role="alert"]'),
      'Failure did not reach the UI')
  } else {
    await until(() => host.getSnapshot().runtime.run.status === 'idle' &&
      host.getSnapshot().runtime.messages.length === 2, 'Text stream did not complete')
    assert.match(document.body.textContent, /你好，这是一个纯文本流式回复/)
    render({ ...config, pageId: 'home' })
    await until(() => document.querySelector('.agent-design-launcher'), 'Home did not render')
    click('打开助手')
    await until(() => !document.getElementById('agent-design-conversation').hidden, 'Panel did not open')
    click('关闭助手')
    render(chatConfig)
    await until(() => document.querySelector('.is-standalone'), 'Chat did not return')
    assert.match(document.body.textContent, /你好，这是一个纯文本流式回复/)
  }
  await setTimeout(250)
  assert.equal(requests.length, 1, 'One user action must not replay or create a second sender')
  assert.equal(host.getSnapshot().runtime.conversation.id, thread)
  assert.match(document.body.textContent, /官方模拟服务.*未连接真实业务/)
})
