import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { setTimeout } from 'node:timers/promises'
import { test } from 'node:test'

const target = resolve(process.argv[2])
const requireTarget = createRequire(resolve(target, 'package.json'))
/** 在本次隔离消费者中解析已安装依赖，不引用另一个工程的 React 或工具源码。 */
async function dependency(name) {
  return import(pathToFileURL(requireTarget.resolve(name)).href)
}

/** 等待真实包完成组件提交；此测试只验证 DOM/Host 身份，不代表 Electron 视觉验收。 */
async function until(read) {
  for (let index = 0; index < 100; index++) {
    if (read()) return
    await setTimeout(40)
  }
  throw new Error('Host state did not settle')
}

test('missing connection stays pending; normal page and panel operations keep one official Runtime', async (t) => {
  const { JSDOM } = await dependency('jsdom')
  const dom = new JSDOM('<div id="root"></div>', { url: 'http://127.0.0.1/' })
  const names = ['window', 'document', 'navigator', 'HTMLElement', 'Element', 'Node',
    'MutationObserver', 'getComputedStyle', 'Event', 'CustomEvent', 'MouseEvent', 'DOMRect', 'ShadowRoot']
  const saved = new Map(names.map((name) => [name, Object.getOwnPropertyDescriptor(globalThis, name)]))
  for (const name of names) Object.defineProperty(globalThis, name, { value: dom.window[name], configurable: true })
  window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} })
  const extraNames = ['ResizeObserver', 'requestAnimationFrame', 'cancelAnimationFrame', 'fetch']
  const extra = new Map(extraNames.map((name) => [name, Object.getOwnPropertyDescriptor(globalThis, name)]))
  globalThis.ResizeObserver = class {
    /** jsdom 不提供实际布局。 */
    observe() {}
    /** jsdom 没有布局订阅。 */
    unobserve() {}
    /** jsdom 没有布局资源。 */
    disconnect() {}
  }
  globalThis.requestAnimationFrame = (callback) => globalThis.setTimeout(() => callback(Date.now()), 0)
  globalThis.cancelAnimationFrame = globalThis.clearTimeout
  const requests = []
  // 本测试没有 Mock 或真实后端；任何隐式默认连接都必须使测试失败。
  globalThis.fetch = (...args) => { requests.push(args); throw new Error('Unexpected request without a user send') }
  const { createServer } = await dependency('vite')
  const server = await createServer({ root: target, configFile: false,
    server: { middlewareMode: true, hmr: false, watch: null }, ssr: { noExternal: [/^@za38\.03\//] } })
  const { createElement } = await dependency('react')
  const { createRoot } = await dependency('react-dom/client')
  const { App } = await server.ssrLoadModule('/src/App.tsx')
  const { designPages } = await server.ssrLoadModule('/src/design/pages.tsx')
  const standalone = designPages.find((page) => page.surface === 'standalone_page')
  const floating = designPages.find((page) => page.surface === 'floating_panel')
  assert.ok(standalone && floating, 'This regression requires both confirmed surface types')
  const { AgentUIProvider, useAgentUI } = await server.ssrLoadModule('@za38.03/agent-ui-runtime')
  let host
  /** 仅测试侧通过公开 Host 观察 Runtime，不向生成页面加入状态面板。 */
  function Observer() { host = useAgentUI(); return null }
  const root = createRoot(document.getElementById('root'))
  t.after(async () => {
    root.unmount()
    await server.close()
    dom.window.close()
    for (const [name, descriptor] of [...saved, ...extra]) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor)
      else delete globalThis[name]
    }
  })
  /** 同一 Host 根只更新实际页面身份和连接 props，不重新创建 Provider。 */
  function render(props) {
    root.render(createElement(AgentUIProvider, null, createElement(App, { ...props,
      onNavigatePage: (pageId) => render({ ...props, pageId }),
    }), createElement(Observer)))
  }
  render({ pageId: standalone.pageId })
  await until(() => document.body.textContent.includes('等待配置设计预览连接'))
  assert.equal(requests.length, 0)
  render({ pageId: standalone.pageId, endpoint: '/design-agent', source: 'real-runtime' })
  await setTimeout(100)
  assert.ok(document.body.textContent.includes('等待配置设计预览连接'))
  assert.equal(requests.length, 0)
  render({ pageId: floating.pageId, endpoint: '/design-agent', source: 'official-mock' })
  await until(() => host?.getSnapshot().status === 'ready')
  await setTimeout(200)
  const threadId = host.getSnapshot().runtime.conversation.id
  const business = document.querySelector('.agent-design-business')
  assert.ok(business, 'Floating page must retain its business body')
  if (floating.hasInlineLauncher) assert.equal(document.querySelector('.agent-design-launcher'), null)
  /** 按当前 DOM 重新取入口；条件渲染可在关闭时替换原按钮节点。 */
  function openPanel() {
    const launcher = document.querySelector('.agent-design-launcher')
      ?? business.querySelector('button[data-ui-effect="open"]')
    assert.ok(launcher, 'Floating page must have a functioning entry')
    launcher.click()
  }
  openPanel()
  await until(() => !document.getElementById('agent-design-conversation').hidden)
  const businessClose = business.querySelector('button[data-ui-effect="close"]')
  if (floating.hasInlineLauncher && businessClose) {
    businessClose.click()
    await until(() => document.getElementById('agent-design-conversation').hidden)
    openPanel()
    await until(() => !document.getElementById('agent-design-conversation').hidden)
  }
  document.querySelector('.agent-design-close').click()
  await until(() => document.getElementById('agent-design-conversation').hidden)
  render({ pageId: standalone.pageId, endpoint: '/design-agent', source: 'official-mock' })
  await until(() => document.querySelector('.is-standalone'))
  assert.equal(host.getSnapshot().runtime.conversation.id, threadId)
  assert.equal(document.getElementById('agent-design-conversation').hidden, false)
  assert.equal(Boolean(document.querySelector('.agent-design-business')), Boolean(standalone.component),
    'Standalone page must retain declared page controls without duplicating the official conversation')
  assert.equal(document.querySelectorAll('textarea').length, 1, 'Only the official composer may be mounted')
  assert.equal(requests.length, 0)
  render({ pageId: 'unknown', endpoint: '/design-agent' })
  await until(() => document.body.textContent.includes('当前页面不属于本轮'))
  assert.equal(document.querySelector('.agent-design-conversation'), null)
})
