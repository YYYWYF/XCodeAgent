import assert from 'node:assert/strict'
import { setTimeout } from 'node:timers/promises'
import { JSDOM } from 'jsdom'
import { createServer } from 'vite'
import { createRequire } from 'node:module'
import path from 'node:path'
import { pathToFileURL } from 'node:url'
import { cp, mkdtemp, mkdir, readFile, symlink, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { createConsumerMockServer } from '../../agent-ui/mock-consumer.mjs'

/** 等待真实组件状态收敛，超时保留页面文本帮助定位集成失败。 */
export async function until(read, message) {
  for (let i = 0; i < 200; i++) {
    if (read()) return
    await setTimeout(40)
  }
  throw new Error(message + '\n' + document.body.textContent)
}

/** 为真实生成应用层提供测试 DOM 与官方 HTTP Mock，不替换 Agent 或解析 SSE。 */
export async function mountConsumer(t, scenario = 'simple-chat', sharedProject) {
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
  let selectedContext = {}
  /** 只在测试中观察公开 Host 状态，不向成品页面添加调试状态或控制按钮。 */
  function Observer() {
    host = useAgentUI()
    return null
  }
  /** 页面用正式 hook 提供当前记录，测试不修改 Agent UI 包。 */
  function PageContext() {
    module.useSharedAgentContext?.(selectedContext)
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
      children: createElement('main', null, '业务首页', sessionKey && createElement(Observer), sessionKey && createElement(PageContext),
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
  return { requests, mock, snapshot: () => host.getSnapshot().runtime, showPage, getHost: () => host,
    setContext: (value) => { selectedContext = value; showPage() },
  }
}

/** 从本仓库的真实应用层准备 Host 回归；独立成品转入另由集成用例验证。 */
export async function mountSharedConsumer(t, scenario, agentId, mockServer, preview = false) {
  const seed = path.resolve(import.meta.dirname, '..')
  const root = await mkdtemp(path.join(tmpdir(), 'agent-ui-host-'))
  t.after(() => rm(root, { recursive: true, force: true }))
  await cp(path.join(seed, 'src/agent-ui'), path.join(root, 'src/agent-ui'), { recursive: true })
  await symlink(path.join(seed, 'node_modules'), path.join(root, 'node_modules'), 'dir')
  await writeFile(path.join(root, 'package.json'), await readFile(path.join(seed, 'package.json')))
  await mkdir(path.join(root, 'src/design'))
  for (const name of ['Agent.tsx', 'SharedHost.tsx', 'SharedApplication.tsx', 'SharedPage.tsx',
    'runtimeResults.ts', 'useRuntimeResult.ts', 'runtimeContext.tsx', 'runtimeRequest.ts', 'RuntimeBusinessPanel.tsx', 'RuntimeInput.tsx', 'shared-host.css']) {
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
      conversation: '智能体会话', close: '关闭助手',
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

/** 通过真实宿主按钮驱动组件。 */
export function click(label) {
  const button = [...document.querySelectorAll('button')].find((item) => item.textContent.includes(label) || item.getAttribute('aria-label') === label)
  assert.ok(button, `Missing button: ${label}`)
  assert.equal(button.disabled, false, label)
  button.click()
}
