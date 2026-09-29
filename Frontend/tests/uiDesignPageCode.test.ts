import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { test } from 'node:test'
import { attachUiDesignPageCode } from '../src/main/uiDesignPageCode'

/** 创建隔离的临时工作区并在测试结束后清理。 */
async function withTemporaryWorkspace(
  run: (workspaceRoot: string) => Promise<void>
): Promise<void> {
  const workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'devagentstudio-ui-design-'))
  try {
    await run(workspaceRoot)
  } finally {
    await fs.rm(workspaceRoot, { force: true, recursive: true })
  }
}

/** 在工作区里写入一页设计稿源码。 */
async function writePageCode(
  workspaceRoot: string,
  pageKey: string,
  code: string
): Promise<void> {
  const pageDir = path.join(
    workspaceRoot,
    '.devagentstudio',
    'ui-design',
    'pages',
    pageKey
  )
  await fs.mkdir(pageDir, { recursive: true })
  await fs.writeFile(path.join(pageDir, 'index.tsx'), code, 'utf8')
}

/** 正式 manifest：只存 code_path 不存 code。 */
function manifest(...pageKeys: string[]): Record<string, unknown> {
  return {
    schema_version: 'ui-manifest.v3',
    pages: pageKeys.map((pageKey) => ({
      pageId: pageKey.toLowerCase(),
      page_key: pageKey,
      status: 'confirmed'
    }))
  }
}

/** 设计稿源码必须按 page_key 从磁盘读回来，否则重新打开工作区后按钮一直禁用。 */
test('给缺 code 的页面从磁盘回填源码', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    const code = 'export default function PageWelcomeHome() { return <div>hello</div>; }\n'
    await writePageCode(workspaceRoot, 'WelcomeHome', code)

    const enriched = (await attachUiDesignPageCode(
      workspaceRoot,
      manifest('WelcomeHome')
    )) as { pages: Array<Record<string, unknown>> }

    assert.equal(enriched.pages[0].code, code)
    // 其余字段原样保留
    assert.equal(enriched.pages[0].status, 'confirmed')
    assert.equal((enriched as Record<string, unknown>).schema_version, 'ui-manifest.v3')
  })
})

/** 本轮还没生成的页面磁盘上没有源码，留空即可 —— 按钮禁用是正确表现。 */
test('磁盘没有源码时保持原样', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    const enriched = (await attachUiDesignPageCode(
      workspaceRoot,
      manifest('HelloAgent')
    )) as { pages: Array<Record<string, unknown>> }

    assert.equal(enriched.pages[0].code, undefined)
  })
})

/** 已经带 code 的条目（生成池刚写完）不做多余的磁盘读取，保持内存里的最新版本。 */
test('已有 code 的条目不覆盖', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    await writePageCode(workspaceRoot, 'WelcomeHome', '// 磁盘上的旧版本')
    const input = manifest('WelcomeHome')
    ;(input.pages as Array<Record<string, unknown>>)[0].code = '// 内存里的最新版本'

    const enriched = (await attachUiDesignPageCode(workspaceRoot, input)) as {
      pages: Array<Record<string, unknown>>
    }

    assert.equal(enriched.pages[0].code, '// 内存里的最新版本')
  })
})

/** 缺 page_key 或结构不符时原样返回，不能抛错阻断读取。 */
test('结构异常时原样返回', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    assert.deepEqual(await attachUiDesignPageCode(workspaceRoot, null), null)
    assert.deepEqual(await attachUiDesignPageCode(workspaceRoot, { pages: 'nope' }), {
      pages: 'nope'
    })
    const noKey = { pages: [{ pageId: 'orphan' }] }
    assert.deepEqual(await attachUiDesignPageCode(workspaceRoot, noKey), noKey)
  })
})
