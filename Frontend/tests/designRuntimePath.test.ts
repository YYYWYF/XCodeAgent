import assert from 'node:assert/strict'
import { join } from 'node:path'

import { resolveDesignRuntimeFile } from '../src/main/designRuntimePath'

// 专用协议只允许 iframe 文档及其 runtime bundle，不能成为任意本地文件读取入口。
assert.equal(
  resolveDesignRuntimeFile('devagentstudio-design://runtime/design-frame.html', '/bundle'),
  join('/bundle', 'design-frame.html')
)
assert.equal(
  resolveDesignRuntimeFile('devagentstudio-design://runtime/antd5-runtime.js', '/bundle'),
  join('/bundle', 'antd5-runtime.js')
)
assert.equal(resolveDesignRuntimeFile('devagentstudio-design://runtime/secret.txt', '/bundle'), null)
assert.equal(resolveDesignRuntimeFile('devagentstudio-design://runtime/../secret.txt', '/bundle'), null)
assert.equal(resolveDesignRuntimeFile('devagentstudio-design://other/design-frame.html', '/bundle'), null)
assert.equal(resolveDesignRuntimeFile('file:///bundle/design-frame.html', '/bundle'), null)

console.log('Design runtime protocol paths passed')
