import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { test } from 'node:test'
import { readManagedWorkspaceApplication } from '../src/main/managedWorkspace'

/** 创建隔离的临时工作区并在测试结束后清理。 */
async function withTemporaryWorkspace(
  run: (workspaceRoot: string) => Promise<void>
): Promise<void> {
  const workspaceRoot = await fs.mkdtemp(path.join(os.tmpdir(), 'xcodeagent-workspace-'))
  try {
    await run(workspaceRoot)
  } finally {
    await fs.rm(workspaceRoot, { force: true, recursive: true })
  }
}

/** 验证真实 .xcodeagent 目录和有效 application.json 可以被识别。 */
test('允许添加规范的 XCodeAgent 本地项目', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    const agentDirectory = path.join(workspaceRoot, '.xcodeagent')
    await fs.mkdir(agentDirectory)
    await fs.writeFile(
      path.join(agentDirectory, 'application.json'),
      JSON.stringify({
        schemaVersion: 3,
        appName: '本地项目',
        authorization: {
          enabled: false,
          runtimeManagementPageEnabled: false
        }
      }),
      'utf8'
    )

    const application = await readManagedWorkspaceApplication(workspaceRoot)
    assert.equal(application.appName, '本地项目')
  })
})

/** 验证旧版 application.json 不会通过当前权限配置契约。 */
test('拒绝非 schemaVersion 3 的工作区配置', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    const agentDirectory = path.join(workspaceRoot, '.xcodeagent')
    await fs.mkdir(agentDirectory)
    await fs.writeFile(
      path.join(agentDirectory, 'application.json'),
      JSON.stringify({ schemaVersion: 2, appName: '旧版项目' }),
      'utf8'
    )

    await assert.rejects(readManagedWorkspaceApplication(workspaceRoot), /schemaVersion 为 3/)
  })
})

/** 验证普通文件夹不能绕过项目标识校验进入应用索引。 */
test('拒绝缺少 .xcodeagent 目录的普通文件夹', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    await assert.rejects(
      readManagedWorkspaceApplication(workspaceRoot),
      /缺少 \.xcodeagent 目录/
    )
  })
})

/** 验证同名文件不能伪装成受管理项目目录。 */
test('拒绝文件形式的 .xcodeagent 标识', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    await fs.writeFile(path.join(workspaceRoot, '.xcodeagent'), 'not-a-directory', 'utf8')
    await assert.rejects(readManagedWorkspaceApplication(workspaceRoot), /必须是真实目录/)
  })
})

/** 验证缺少正式应用配置的标识目录不能生成不完整索引。 */
test('拒绝缺少 application.json 的标识目录', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    await fs.mkdir(path.join(workspaceRoot, '.xcodeagent'))
    await assert.rejects(readManagedWorkspaceApplication(workspaceRoot), /application\.json/)
  })
})
