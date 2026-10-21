import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { spawn } from 'node:child_process'
import WebSocket from 'ws'
const frontend = path.resolve(import.meta.dirname, '..')
const evidence = path.resolve(frontend, '../.validation')
const credentials = path.join(os.homedir(), '.devagentstudio_dev', 'yst-login', 'auth.json')
const checks = []
let processForTest
/** 等待条件，保留失败边界。 */
async function until(operation, description, timeout = 15000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    try { const result = await operation(); if (result) return result } catch {}
    await new Promise(resolve => setTimeout(resolve, 150))
  }
  throw new Error(`等待超时：${description}`)
}
/** 连接本次 Electron 的调试端点。 */
async function connect(url) {
  const socket = new WebSocket(url)
  await new Promise((resolve, reject) => { socket.once('open', resolve); socket.once('error', reject) })
  let nextId = 0
  const pending = new Map()
  socket.on('message', raw => {
    const message = JSON.parse(raw.toString())
    const callback = pending.get(message.id)
    if (!callback) return
    pending.delete(message.id)
    if (message.error) callback.reject(new Error(message.error.message)); else callback.resolve(message.result)
  })
  return { socket,
    call(method, params = {}) {
      const id = ++nextId
      return new Promise((resolve, reject) => { pending.set(id, { resolve, reject }); socket.send(JSON.stringify({ id, method, params })) })
    },
    async evaluate(expression) {
      const result = await this.call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
      if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails))
      return result.result?.value
    }
  }
}
/** 查找实际 Electron 页面。 */
async function page(suffix) {
  return until(async () => {
    const pages = await (await fetch('http://127.0.0.1:18311/json')).json()
    return pages.find(item => item.url.includes(`/out/renderer/${suffix}.html`))
  }, `Electron ${suffix} 页面`)
}
/** 启动目标副本，使用本机模拟认证后端。 */
async function start() {
  const env = { ...process.env, DEVAGENTSTUDIO_BACKEND_URL: process.env.DEVAGENTSTUDIO_BACKEND_URL || 'http://127.0.0.1:18060' }
  delete env.ELECTRON_RUN_AS_NODE
  processForTest = spawn(path.join(frontend, 'node_modules/electron/dist/electron.exe'), [frontend, '--inspect=18310', '--remote-debugging-port=18311'], { cwd: frontend, windowsHide: true, env, stdio: ['ignore', 'pipe', 'pipe'] })
  const output = await fs.open(path.join(evidence, 'electron.log'), 'a')
  processForTest.stdout.on('data', chunk => { void output.write(chunk) })
  processForTest.stderr.on('data', chunk => { void output.write(chunk) })
  processForTest.once('exit', () => { void output.close() })
  await page('login')
}
/** 使用真实 app.quit 验证退出清理。 */
async function quit() {
  const targets = await (await fetch('http://127.0.0.1:18310/json')).json()
  const main = await connect(targets[0].webSocketDebuggerUrl)
  await main.evaluate(`process.getBuiltinModule('module').createRequire(process.cwd() + '/package.json')('electron').app.quit()`)
  main.socket.close()
  await until(() => processForTest.exitCode !== null, 'Electron 正常退出', 25000)
}
/** 检查凭据是否存在，不输出任何凭据值。 */
async function exists() { return fs.stat(credentials).then(() => true, () => false) }
try {
  await start()
  let login = await connect((await page('login')).webSocketDebuggerUrl)
  await until(() => login.evaluate(`document.body.innerText.includes('使用云效登录')`), '登录界面渲染')
  assert.equal(await login.evaluate(`typeof window.devAgentStudio.auth.getAccessToken`), 'undefined')
  checks.push('渲染器没有 token 读取接口')
  const fence = JSON.parse(await fs.readFile(path.join(path.dirname(credentials), 'session.json'), 'utf8'))
  assert.equal(fence.active, false)
  checks.push('点击登录前未初始化 Python 认证会话')
  if (process.argv.includes('--startup-only')) {
    const status = await login.evaluate('window.devAgentStudio.auth.status()')
    assert.equal(status.authenticated, false)
    checks.push('Python 未启动时仍正常显示登录窗口')
    await quit()
    login.socket.close()
    assert.equal(await exists(), false)
    await fs.writeFile(path.join(evidence, 'startup-result.json'), JSON.stringify({ passed: true, checks }, null, 2))
    console.log(JSON.stringify({ passed: true, checks }, null, 2))
  } else {
  for (const theme of ['light', 'dark']) {
    await login.evaluate(`window.devAgentStudio.settings.saveTheme({theme:'${theme}'})`)
    await until(() => login.evaluate(`document.querySelector('[data-theme]')?.getAttribute('data-theme') === '${theme}'`), `${theme} 主题`)
    const screenshot = await login.call('Page.captureScreenshot', { format: 'png' })
    await fs.writeFile(path.join(evidence, `login-${theme}.png`), Buffer.from(screenshot.data, 'base64'))
    checks.push(`实际 Electron ${theme} 登录主题截图`)
  }
  await login.evaluate(`[...document.querySelectorAll('button')].find(button=>button.textContent.includes('使用云效登录')).click()`)
  await until(() => login.evaluate(`document.body.innerText.includes('请在浏览器中完成云效授权')`), '等待授权进度')
  await login.evaluate(`[...document.querySelectorAll('button')].find(button=>button.textContent.includes('取消登录')).click()`)
  await until(() => login.evaluate(`document.body.innerText.includes('使用云效登录')`), '取消后恢复按钮')
  assert.equal(await exists(), false)
  checks.push('取消授权后没有凭据文件，并可重试')
  await login.evaluate(`[...document.querySelectorAll('button')].find(button=>button.textContent.includes('使用云效登录')).click()`)
  await until(() => login.evaluate(`document.body.innerText.includes('请在浏览器中完成云效授权')`), '再次等待授权')
  await fetch('http://127.0.0.1:18061/test/approve')
  const main = await connect((await page('index')).webSocketDebuggerUrl)
  login.socket.close()
  const encrypted = await fs.readFile(credentials, 'utf8')
  assert.ok(!encrypted.includes('synthetic-electron-test-token'))
  assert.ok(!encrypted.includes('synthetic-refresh-token'))
  const status = await main.evaluate('window.devAgentStudio.auth.status()')
  assert.equal(status.authenticated, true)
  assert.equal(status.user.id, 'electron-test-user')
  checks.push('授权后加密落盘，主窗口读取 Python 登录状态')
  await main.evaluate('window.close()')
  assert.equal(await exists(), true)
  checks.push('关闭窗口进入托盘时保留凭据')
  await quit()
  main.socket.close()
  assert.equal(await exists(), false)
  checks.push('真正 app.quit 后删除凭据文件')
  await fs.writeFile(credentials, encrypted)
  await start()
  login = await connect((await page('login')).webSocketDebuggerUrl)
  assert.equal(await exists(), false)
  checks.push('冷启动删除残留凭据并显示登录窗口')
  await quit()
  login.socket.close()
  await fs.writeFile(path.join(evidence, 'electron-result.json'), JSON.stringify({ passed: true, checks }, null, 2))
  console.log(JSON.stringify({ passed: true, checks }, null, 2))
  }
} finally {
  if (processForTest && processForTest.exitCode === null) processForTest.kill()
}
