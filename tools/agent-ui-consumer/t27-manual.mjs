import { spawn } from 'node:child_process'
import { cp, mkdtemp, mkdir, realpath, symlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createServer, build, loadConfigFromFile } from 'vite'
import { HttpAgent } from '@ag-ui/client'

const seed = path.dirname(fileURLToPath(import.meta.url))
const python = process.env.AGENT_UI_TEST_PYTHON
if (!python) throw new Error('请通过 AGENT_UI_TEST_PYTHON 指定已有 Runtime 的 Python 解释器。')
const root = await realpath(await mkdtemp(path.join(tmpdir(), 'xcodeagent-t27-manual-')))
await mkdir(path.join(root, 'src/design'), { recursive: true })
await cp(path.join(seed, 'src/agent-ui'), path.join(root, 'src/agent-ui'), { recursive: true })
await symlink(path.join(seed, 'node_modules'), path.join(root, 'node_modules'), 'dir')
for (const name of ['package.json', 'tsconfig.json', 'vite.config.ts', 'eslint.config.mjs']) await cp(path.join(seed, name), path.join(root, name))
for (const name of ['Agent.tsx', 'SharedHost.tsx', 'SharedApplication.tsx', 'SharedPage.tsx', 'runtimeContext.tsx',
  'runtimeRequest.ts', 'RuntimeBusinessPanel.tsx', 'RuntimeInput.tsx', 'runtimeResults.ts', 'useRuntimeResult.ts', 'shared-host.css']) {
  await cp(path.join(seed, '../agent-ui-design', name), path.join(root, 'src/design', name))
}
await writeFile(path.join(root, 'src/design/props.ts'), 'export const agentProps = {welcome:{title:"订单助手",description:"可查询、更新或澄清测试订单"}}')
await writeFile(path.join(root, 'src/design/presentation.ts'), 'export const pagePresentation: Record<string,{props:object;css:string}> = {}')
await writeFile(path.join(root, 'src/design/surfaces.ts'), `export const agentSurfaces = [
  {pageId:'orders',surface:'floating_panel',entries:[{actionId:'ask',label:'打开订单助手'}]}] as const`)
await writeFile(path.join(root, 'index.html'), '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>T27 业务联调</title><div id="root"></div><script type="module" src="/src/main.tsx"></script></html>')
await writeFile(path.join(root, 'src/main.tsx'), `import {useState,type ReactElement} from 'react'
import {createRoot} from 'react-dom/client'
import {SharedAgentApplication,useSharedAgentContext} from './design/SharedApplication'
import './manual.css'
/** 当前记录通过正式页面 hook 进入 Runtime，页面无需复制聊天客户端。 */
function Orders():ReactElement {
  const [order,setOrder]=useState('ORDER-100')
  useSharedAgentContext({actionId:'ask',contextItems:[{id:'orderId',label:'订单',value:order}]})
  return <main><h1>T27 订单业务联调</h1>
    <p>隔离测试数据 · 真实 Python Runtime / DeepAgents / 工具 / checkpoint · 确定性测试模型</p>
    <label>当前订单 <select value={order} onChange={event=>setOrder(event.target.value)}>
      <option>ORDER-100</option><option>ORDER-200</option></select></label>
    <p>在助手中依次发送“查询订单”“更新订单”“澄清订单”，通过按钮处理待办。</p>
    <p>同意更新会增加当前订单的写入次数，拒绝不会写入。查询订单可核对实际次数。</p>
    <p>关闭后重新打开助手，或切换订单，继续核对消息与当前记录。</p>
  </main>
}
/** 测试应用采用与正式生成器相同的共享 Host 和同源 Python 路由。 */
function App():ReactElement {return <SharedAgentApplication sessionKey="t27-manual" agentId="assistant"
  pageId="orders" endpoint="/agents/assistant/run"><Orders/></SharedAgentApplication>}
const root=document.getElementById('root')
if(!root)throw new Error('Missing root')
createRoot(root).render(<App/>)
`)
await writeFile(path.join(root, 'src/manual.css'), `html,body,#root{margin:0;min-height:100%;font-family:system-ui,sans-serif;color:CanvasText;background:Canvas;color-scheme:light dark}main{padding:1.5rem;max-width:36rem}select{font:inherit;color:CanvasText;background:Canvas}p{line-height:1.6}`)
const backend = spawn(python, [path.join(seed, 'tests/python_runtime_fixture.py'), '--template-root',
  process.env.AGENT_UI_TEMPLATE_ROOT ?? path.resolve(seed, '../../../springboot-template')], { stdio: ['ignore', 'pipe', 'pipe'] })
let server
let closed = false
/** 只停止本脚本拥有的服务，保留已有应用和 Runtime。 */
async function close() {
  if (closed) return
  closed = true
  await server?.close()
  if (backend.exitCode === null) {
    const stopped = new Promise(resolve => backend.once('exit', resolve))
    backend.kill('SIGTERM')
    await stopped
  }
}
process.once('SIGTERM', () => { void close() })
process.once('SIGINT', () => { void close() })
backend.stderr.on('data', data => process.stderr.write(data))
try {
  const port = await new Promise((resolve, reject) => {
    let output = ''
    const timeout = setTimeout(() => reject(new Error('Python 启动超时')), 15000)
    backend.once('error', reject)
    backend.once('exit', code => { clearTimeout(timeout); reject(new Error('Python 已退出：' + code)) })
    backend.stdout.on('data', data => {
      output += data
      const line = output.split('\n').find(value => value.startsWith('{"port":'))
      if (line) { clearTimeout(timeout); resolve(JSON.parse(line).port) }
    })
  })
  const loaded = await loadConfigFromFile({ command: 'serve', mode: 'development' }, path.join(root, 'vite.config.ts'))
  // 替换消费者的 Mock 代理配置；/agent 前缀会先匹配 /agents，不能与业务代理合并。
  const options = { ...loaded.config, root, configFile: false, server: { host: 'localhost', port: 5199, strictPort: true,
    proxy: { '/agents': { target: 'http://127.0.0.1:' + port, changeOrigin: true } } } }
  if (process.argv.includes('--verify')) {
    const checked = spawn(path.join(seed, 'node_modules/.bin/tsc'), ['--noEmit'], { cwd: root, stdio: 'inherit' })
    const code = await new Promise(resolve => checked.once('exit', resolve))
    if (code !== 0) throw new Error('联调应用类型检查失败')
    await build(options)
    const lint = spawn(path.join(seed, 'node_modules/.bin/eslint'), ['src/design', 'src/main.tsx'], { cwd: root, stdio: 'inherit' })
    if (await new Promise(resolve => lint.once('exit', resolve)) !== 0) throw new Error('联调应用 lint 失败')
  }
  server = await createServer(options)
  await server.listen()
  const address = server.httpServer.address()
  // 对用户将要访问的同一地址执行只读 SDK 请求，启动成功不能只以 HTML 可加载判断。
  const probe = new HttpAgent({ url: 'http://localhost:' + address.port + '/agents/assistant/run',
    threadId: 'manual-startup-check', initialMessages: [], initialState: {} })
  await probe.runAgent({ forwardedProps: { inspectInteraction: true } })
  console.log('Electron 预览地址：http://localhost:' + address.port)
  console.log('临时联调工程：' + root)
  console.log('此页使用测试模型；真实业务工具和 checkpoint 仅操作隔离内存/临时目录。按 Ctrl+C 停止。')
} catch (error) { await close(); throw error }
