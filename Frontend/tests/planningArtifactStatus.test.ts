import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { test } from 'node:test'
import {
  endpointDesignDocumentExists,
  endpointDesignDocumentPath,
  PRODUCT_PLAN_SCHEMA_VERSION
} from '../src/main/planningArtifactStatus'

/** 验证 Electron 工作台只读取后端当前的 ProductPlan v5 契约。 */
test('ProductPlan 工作台校验使用 v5', () => {
  assert.equal(PRODUCT_PLAN_SCHEMA_VERSION, 'product-plan.v5')
})

/** 创建隔离工作区并在用例结束后清理。 */
async function withTemporaryWorkspace(
  run: (workspaceRoot: string) => Promise<void>
): Promise<void> {
  const workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'xcodeagent-endpoint-status-'))
  try {
    await run(workspaceRoot)
  } finally {
    await fs.rm(workspaceRoot, { force: true, recursive: true })
  }
}

/** 验证 TechnicalPlan 中声明 endpoint 但没有独立文档时仍保持待设计。 */
test('没有 endpoint Markdown 时接口保持待设计', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    assert.equal(
      await endpointDesignDocumentExists(workspaceRoot, 'employee-api', 'employee.list'),
      false
    )
  })
})

/** 验证空文件不属于已产出的有效 endpoint 设计文档。 */
test('空 endpoint Markdown 不会标记为已设计', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    const documentPath = endpointDesignDocumentPath(
      workspaceRoot,
      'employee-api',
      'employee.list'
    )
    await fs.mkdir(path.dirname(documentPath), { recursive: true })
    await fs.writeFile(documentPath, '   \n', 'utf8')

    assert.equal(
      await endpointDesignDocumentExists(workspaceRoot, 'employee-api', 'employee.list'),
      false
    )
  })
})

/** 验证只有非空用户可读 endpoint 文档真实落盘后才标记为已设计。 */
test('非空 endpoint Markdown 产出后接口标记为已设计', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    const documentPath = endpointDesignDocumentPath(
      workspaceRoot,
      'employee-api',
      'employee.list'
    )
    await fs.mkdir(path.dirname(documentPath), { recursive: true })
    await fs.writeFile(documentPath, '# Employee list endpoint\n', 'utf8')

    assert.equal(
      await endpointDesignDocumentExists(workspaceRoot, 'employee-api', 'employee.list'),
      true
    )
  })
})

/** 验证内部 JSON 单独存在时不能代替用户要求的 endpoint 文档。 */
test('只有 endpoint JSON 时接口仍保持待设计', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    const markdownPath = endpointDesignDocumentPath(
      workspaceRoot,
      'employee-api',
      'employee.list'
    )
    await fs.mkdir(path.dirname(markdownPath), { recursive: true })
    await fs.writeFile(markdownPath.replace(/\.md$/, '.json'), '{"status":"confirmed"}\n', 'utf8')

    assert.equal(
      await endpointDesignDocumentExists(workspaceRoot, 'employee-api', 'employee.list'),
      false
    )
  })
})
