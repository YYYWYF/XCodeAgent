import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { test } from 'node:test'
import { readWorkspaceUiDesigns } from '../src/main/uiDesignSnapshot.ts'

/** 构造最小工作区并验证状态与 TSX 源码始终由同一份磁盘快照返回。 */
test('读取已确认设计稿的真实 TSX，同时阻止越界和缺失源码伪装成可预览', async () => {
  const workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'xcodeagent-ui-snapshot-'))
  try {
    const specs = path.join(workspaceRoot, '.xcodeagent', 'specs')
    const pages = path.join(workspaceRoot, '.xcodeagent', 'ui-design', 'pages')
    const validFile = path.join(pages, 'Home', 'index.tsx')
    await fs.mkdir(path.dirname(validFile), { recursive: true })
    await fs.mkdir(specs, { recursive: true })
    await fs.writeFile(validFile, 'export default function Home() { return <main>首页</main> }')
    await fs.writeFile(path.join(workspaceRoot, 'secret.tsx'), 'secret')
    await fs.writeFile(path.join(specs, 'ui-designs.json'), JSON.stringify({
      pages: [
        { pageId: 'home', status: 'confirmed', code_path: validFile },
        { pageId: 'missing', status: 'confirmed', code_path: path.join(pages, 'Missing', 'index.tsx') },
        { pageId: 'outside', status: 'confirmed', code_path: path.join(workspaceRoot, 'secret.tsx') }
      ]
    }))

    const snapshot = await readWorkspaceUiDesigns(workspaceRoot)
    const result = snapshot?.pages as Array<{ code?: string; code_error?: string }>
    assert.match(result[0].code || '', /<main>首页<\/main>/)
    assert.equal(result[1].code, '')
    assert.match(result[1].code_error || '', /无法读取/)
    assert.equal(result[2].code, '')
    assert.match(result[2].code_error || '', /不在当前项目/)
  } finally {
    await fs.rm(workspaceRoot, { recursive: true, force: true })
  }
})
