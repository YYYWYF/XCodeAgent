/* eslint-disable @typescript-eslint/explicit-function-return-type */
import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url))
const frontendRoot = path.resolve(scriptDirectory, '..')
const electronBinary = (await import('electron')).default
const qaDirectory = await fs.mkdtemp(path.join(os.tmpdir(), 'xcodeagent-agent-ui-electron-'))
const runtimeUrl = pathToFileURL(
  path.join(frontendRoot, 'src/renderer/public/design-runtime/antd5-runtime.js')
).href

const sharedConfig = {
  templateVersion: 'agent-ui.v1',
  agentId: 'qa_agent',
  name: '订单助手',
  responsibility: '分析订单并协助跟进',
  actionId: 'open_qa_agent',
  contextItems: [{ id: 'selected_order', label: '选中订单', value: 'ORD-001' }],
  capabilities: [{ id: 'analyze_orders', label: '分析订单' }],
  suggestedQuestions: ['本周有哪些异常订单？'],
  features: { attachments: false, approvals: true, tools: true, maximize: false },
  mock: {
    userMessage: '分析本周订单',
    assistantMessage: '我会先检查异常订单。',
    toolTitle: '查询订单',
    toolDetail: '已读取 18 条订单。',
    approvalTitle: '需要确认',
    approvalDetail: '是否继续？',
    successMessage: '分析完成',
    errorMessage: '模拟连接中断'
  }
}

/** 生成只加载已打包 Agent UI Runtime 的本地 Electron 验收页。 */
function buildQaHtml() {
  const configs = {
    standalone_page: { ...sharedConfig, surface: 'standalone_page' },
    floating_panel: { ...sharedConfig, surface: 'floating_panel' }
  }
  return `<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <style>html,body,#root{height:100%;margin:0}body{overflow:hidden}</style>
</head>
<body>
  <div id="root"></div>
  <script>
    window.__QA_ERRORS__ = [];
    window.addEventListener('error', function recordError(event) {
      window.__QA_ERRORS__.push(String(event.message || event.error || 'unknown error'));
    });
    window.addEventListener('unhandledrejection', function recordRejection(event) {
      window.__QA_ERRORS__.push(String(event.reason || 'unknown rejection'));
    });
  </script>
  <script src="${runtimeUrl}"></script>
  <script>
    const runtime = window.__DESIGN_RUNTIME__;
    const configs = ${JSON.stringify(configs)};
    const root = runtime.ReactDOMClient.createRoot(document.getElementById('root'));
    let mountSequence = 0;

    /** 按指定 Surface 重新挂载固定 Agent UI。 */
    window.qaMount = function qaMount(surface) {
      const Component = surface === 'standalone_page'
        ? runtime.agentUi.AgentConversationTemplate
        : runtime.agentUi.AgentFloatingPanelTemplate;
      root.render(runtime.React.createElement(Component, {
        configJson: JSON.stringify(configs[surface]),
        key: surface + '-' + String(++mountSequence)
      }));
    };

    /** 点击固定组件内指定文字的主题控件。 */
    window.qaClickText = function qaClickText(text) {
      const candidates = Array.from(document.querySelectorAll('button'));
      const target = candidates.find(function findCandidate(element) {
        const candidateText = element.textContent ? element.textContent.replaceAll(' ', '').trim() : '';
        return candidateText === text.replaceAll(' ', '').trim();
      });
      if (!target) throw new Error('未找到控件：' + text);
      target.click();
    };

    /** 返回当前渲染结构、可访问性和响应式关键事实。 */
    window.qaSnapshot = function qaSnapshot() {
      const launcher = document.querySelector('.x-agent-floating__launcher');
      const panel = document.querySelector('.x-agent-floating__panel');
      const sidebar = document.querySelector('.x-agent-conversation__sidebar');
      const mobileMenu = document.querySelector('.x-agent-conversation__mobile-menu');
      const rootElement = document.querySelector('.x-agent-ui');
      return {
        text: document.body.innerText,
        errors: window.__QA_ERRORS__.slice(),
        overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
        panelOpen: Boolean(panel),
        hasDrawer: Boolean(document.querySelector('.ant-drawer')),
        launcherRect: launcher ? launcher.getBoundingClientRect().toJSON() : null,
        launcherFocused: document.activeElement === launcher,
        sidebarDisplay: sidebar ? getComputedStyle(sidebar).display : null,
        mobileMenuDisplay: mobileMenu ? getComputedStyle(mobileMenu).display : null,
        background: rootElement
          ? getComputedStyle(rootElement).getPropertyValue('--agent-ui-bg').trim()
          : '',
        hasLiveRegion: Boolean(document.querySelector('[aria-live="polite"]')),
        hasComposerLabel: Boolean(document.querySelector('textarea[aria-label="向订单助手提问"]'))
      };
    };

    window.qaMount('floating_panel');
  </script>
</body>
</html>`
}

const qaHtmlPath = path.join(qaDirectory, 'agent-ui-qa.html')
const qaMainPath = path.join(qaDirectory, 'main.cjs')
await fs.writeFile(qaHtmlPath, buildQaHtml(), 'utf8')
await fs.writeFile(
  qaMainPath,
  `const assert = require('node:assert/strict')
const path = require('node:path')
const { app, BrowserWindow, session } = require('electron')

const qaDirectory = process.env.XCODEAGENT_AGENT_UI_QA_DIR
const qaHtmlPath = path.join(qaDirectory, 'agent-ui-qa.html')
const networkRequests = []

/** 等待 React 状态和布局完成。 */
function settle() {
  return new Promise(function resolveAfterRender(resolve) { setTimeout(resolve, 120) })
}

/** 读取验收页公开的确定性快照。 */
async function snapshot(window) {
  await settle()
  return window.webContents.executeJavaScript('window.qaSnapshot()')
}

/** 保存当前真实 Electron 窗口截图。 */
async function capture(window, name) {
  const image = await window.webContents.capturePage()
  await require('node:fs/promises').writeFile(path.join(qaDirectory, name), image.toPNG())
}

/** 执行渲染器验收动作，并在失败时保留动作名称。 */
async function execute(window, label, code) {
  try {
    return await window.webContents.executeJavaScript(code)
  } catch (error) {
    throw new Error(label + ': ' + String(error && error.message ? error.message : error))
  }
}

/** 执行桌面与移动宽度下的固定 Agent UI 回归矩阵。 */
async function run() {
  session.defaultSession.webRequest.onBeforeRequest(function recordRequest(details, callback) {
    if (/^https?:/.test(details.url)) networkRequests.push(details.url)
    callback({})
  })
  const window = new BrowserWindow({
    show: true,
    width: 1440,
    height: 900,
    webPreferences: { contextIsolation: true, nodeIntegration: false }
  })
  await window.loadFile(qaHtmlPath)
  await settle()

  await execute(window, '打开桌面浮窗', "document.querySelector('.x-agent-floating__launcher').click()")
  let current = await snapshot(window)
  assert.match(current.text, /我会先检查异常订单。/)
  assert.equal(current.overflow, false)
  assert.equal(current.hasLiveRegion, true)
  assert.equal(current.hasComposerLabel, true)
  const lightBackground = current.background
  await capture(window, 'floating-desktop-light.png')

  await execute(window, '切换桌面浮窗主题', "window.qaClickText('深色')")
  current = await snapshot(window)
  assert.notEqual(current.background, lightBackground)
  await execute(window, '停止桌面浮窗运行', "document.querySelector('.x-agent-composer button').click()")
  current = await snapshot(window)
  assert.match(current.text, /生成已停止/)
  assert.deepEqual(current.errors, [])

  await execute(window, '重挂载浮窗', "window.qaMount('floating_panel')")
  await settle()
  await execute(window, '重新打开浮窗', "document.querySelector('.x-agent-floating__launcher').click()")
  await execute(window, '切换浮窗移动验收主题', "window.qaClickText('深色')")

  await window.webContents.executeJavaScript(
    "document.querySelector('button[aria-label=\\"关闭会话面板\\"]').click()"
  )
  await settle()
  for (const viewport of [
    { width: 1024, height: 800 },
    { width: 768, height: 720 },
    { width: 767, height: 720 },
    { width: 320, height: 700 }
  ]) {
    window.setContentSize(viewport.width, viewport.height)
    await settle()
    const compactBefore = await snapshot(window)
    await execute(window, '打开响应式浮窗 ' + viewport.width, "document.querySelector('.x-agent-floating__launcher').click()")
    current = await snapshot(window)
    assert.match(current.text, /我会先检查异常订单。/)
    assert.equal(current.overflow, false)
    assert.equal(current.hasDrawer, false)
    if (viewport.width === 320) await capture(window, 'floating-mobile-dark.png')
    window.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Escape' })
    window.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Escape' })
    current = await snapshot(window)
    assert.equal(current.panelOpen, false)
    assert.equal(current.launcherFocused, true)
    assert.deepEqual(current.errors, [])
    assert.ok(compactBefore.launcherRect.x >= 0)
  }

  window.setContentSize(1440, 900)
  await execute(window, '挂载独立页', "window.qaMount('standalone_page')")
  current = await snapshot(window)
  assert.match(current.text, /我会先检查异常订单。/)
  assert.notEqual(current.sidebarDisplay, 'none')
  assert.equal(current.overflow, false)
  await capture(window, 'standalone-desktop-light.png')
  const standaloneLightBackground = current.background
  await execute(window, '切换独立页主题', "window.qaClickText('深色')")
  current = await snapshot(window)
  assert.notEqual(current.background, standaloneLightBackground)
  await execute(window, '停止独立页运行', "document.querySelector('.x-agent-composer button').click()")
  current = await snapshot(window)
  assert.match(current.text, /生成已停止/)
  assert.deepEqual(current.errors, [])

  await execute(window, '重挂载独立页', "window.qaMount('standalone_page')")
  await settle()

  for (const viewport of [
    { width: 1024, height: 800 },
    { width: 768, height: 720 },
    { width: 767, height: 720 },
    { width: 320, height: 700 }
  ]) {
    window.setContentSize(viewport.width, viewport.height)
    await settle()
    current = await snapshot(window)
    assert.equal(current.overflow, false)
    if (viewport.width < 768) {
      assert.equal(current.sidebarDisplay, 'none')
      assert.notEqual(current.mobileMenuDisplay, 'none')
      if (viewport.width === 320) await capture(window, 'standalone-mobile-light.png')
    } else {
      assert.notEqual(current.sidebarDisplay, 'none')
    }
  }
  assert.deepEqual(networkRequests, [])
  window.destroy()
  console.log('agent UI Electron runtime tests passed')
  console.log('screenshots: ' + qaDirectory)
}

app.whenReady().then(run).then(function finish() {
  app.quit()
}).catch(function fail(error) {
  console.error(error)
  app.exit(1)
})
`,
  'utf8'
)

/** 在项目已安装的 Electron 中运行独立验收进程并转发结果。 */
function runElectronQa() {
  return new Promise((resolve, reject) => {
    const child = spawn(electronBinary, [qaMainPath], {
      cwd: frontendRoot,
      env: {
        ...process.env,
        ELECTRON_DISABLE_SECURITY_WARNINGS: 'true',
        XCODEAGENT_AGENT_UI_QA_DIR: qaDirectory
      },
      stdio: 'inherit'
    })
    child.once('error', reject)
    child.once('exit', (code) => {
      if (code === 0) resolve()
      else reject(new Error(`Electron Agent UI 验收失败，退出码 ${String(code)}`))
    })
  })
}

assert.equal(typeof electronBinary, 'string')
await runElectronQa()
