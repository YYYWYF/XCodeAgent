import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { resolveDesignRuntimeFile } from '../src/main/designRuntimePath'
import { compileTsx } from '../src/renderer/src/components/DesignRenderer/compileTsx'

// 专用协议只允许 iframe 文档及其 runtime bundle，不能成为任意本地文件读取入口。
assert.equal(
  resolveDesignRuntimeFile('xcodeagent-design://runtime/design-frame.html', '/bundle'),
  join('/bundle', 'design-frame.html')
)
assert.equal(
  resolveDesignRuntimeFile('xcodeagent-design://runtime/antd5-runtime.js', '/bundle'),
  join('/bundle', 'antd5-runtime.js')
)
assert.equal(resolveDesignRuntimeFile('xcodeagent-design://runtime/secret.txt', '/bundle'), null)
assert.equal(resolveDesignRuntimeFile('xcodeagent-design://runtime/../secret.txt', '/bundle'), null)
assert.equal(resolveDesignRuntimeFile('xcodeagent-design://other/design-frame.html', '/bundle'), null)
assert.equal(resolveDesignRuntimeFile('file:///bundle/design-frame.html', '/bundle'), null)

// React 18 异步提交必须由错误边界回传真实结果，禁止 root.render 后立即宣告成功。
const frameHtml = readFileSync(
  join(process.cwd(), 'src', 'renderer', 'public', 'design-runtime', 'design-frame.html'),
  'utf8'
)
assert.match(frameHtml, /class DesignRuntimeErrorBoundary/)
assert.match(frameHtml, /componentDidCatch\(error\)/)
assert.match(frameHtml, /设计稿渲染结果为空/)
assert.doesNotMatch(
  frameHtml,
  /root\.render\(runtime\.React\.createElement\(component\)\);\s*window\.parent\.postMessage/
)

// 只具名导入 React hook 的截图稿不能因经典 JSX 转换引用未定义的 React 而白屏。
const react = {
  Fragment: Symbol('Fragment'),
  useState: <T>(initial: T): [T, (next: T) => void] => [initial, () => undefined],
  createElement: (type: unknown, props: unknown, ...children: unknown[]) => ({
    type,
    props,
    children
  })
}
const runtimeWindow: Record<string, unknown> = {
  __DESIGN_RUNTIME__: { React: react }
}
const namedOnly = compileTsx(
  "import { useState } from 'react'; export default function Page() { const [label] = useState('可预览'); return <><main>{label}</main></>; }"
)
assert.match(namedOnly, /window\.__DESIGN_RUNTIME__\.React\.createElement/)
new Function('window', namedOnly)(runtimeWindow)
const component = runtimeWindow.__DESIGN_COMPONENT__ as () => { type: unknown; children: unknown[] }
const element = component()
assert.equal(element.type, react.Fragment)
assert.equal((element.children[0] as { type: unknown }).type, 'main')
assert.equal((element.children[0] as { children: unknown[] }).children[0], '可预览')

console.log('Design runtime protocol paths passed')
