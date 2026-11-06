import assert from 'node:assert/strict'
import { join } from 'node:path'
import { runInNewContext } from 'node:vm'
import * as React from 'react'

import { resolveDesignRuntimeFile } from '../src/main/designRuntimePath'
import { compileTsx } from '../src/renderer/src/components/DesignRenderer/compileTsx'

// 普通业务稿仍转译为可执行 React；旧虚拟模块及动态导入不能进入运行时。
const frame = { __DESIGN_RUNTIME__: { React } } as Record<string, unknown>
runInNewContext(
  compileTsx(
    "import React from 'react'; export default function Page(){return <main>业务页面</main>}"
  ),
  { window: frame }
)
assert.equal(typeof frame.__DESIGN_COMPONENT__, 'function')
for (const source of [
  "import { AgentConversationTemplate } from '@xcodeagent/agent-ui-design'; export default AgentConversationTemplate",
  "export default () => import('@xcodeagent/agent-ui-design')"
])
  assert.throws(() => compileTsx(source), /重新生成/)
assert.throws(() => compileTsx("import Other from 'unregistered'; export default Other"), /不支持/)

// 专用协议只允许 iframe 文档及其 runtime bundle，不能成为任意本地文件读取入口。
assert.equal(
  resolveDesignRuntimeFile('devagentstudio-design://runtime/design-frame.html', '/bundle'),
  join('/bundle', 'design-frame.html')
)
assert.equal(
  resolveDesignRuntimeFile('devagentstudio-design://runtime/antd5-runtime.js', '/bundle'),
  join('/bundle', 'antd5-runtime.js')
)
assert.equal(
  resolveDesignRuntimeFile('devagentstudio-design://runtime/secret.txt', '/bundle'),
  null
)
assert.equal(
  resolveDesignRuntimeFile('devagentstudio-design://runtime/../secret.txt', '/bundle'),
  null
)
assert.equal(
  resolveDesignRuntimeFile('devagentstudio-design://other/design-frame.html', '/bundle'),
  null
)
assert.equal(resolveDesignRuntimeFile('file:///bundle/design-frame.html', '/bundle'), null)

console.log('Design runtime protocol paths passed')
