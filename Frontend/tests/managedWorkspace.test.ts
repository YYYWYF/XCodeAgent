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

/** 验证真实 .xcodeagent 目录和有效 v5 application.json 可以被识别。 */
test('允许添加规范的 XCodeAgent 本地项目', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    const agentDirectory = path.join(workspaceRoot, '.xcodeagent')
    await fs.mkdir(agentDirectory)
    await fs.writeFile(
      path.join(agentDirectory, 'application.json'),
      JSON.stringify({
        schemaVersion: 5,
        appName: '本地项目',
        auth: { enable: false },
        authorization: {
          enabled: false,
          initialAdministratorSubjects: []
        }
      }),
      'utf8'
    )

    const application = await readManagedWorkspaceApplication(workspaceRoot)
    assert.equal(application.appName, '本地项目')
  })
})

/** 验证旧结构因不是当前 schemaVersion 而不能通过。 */
test('拒绝缺少当前权限字段的旧结构', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    const agentDirectory = path.join(workspaceRoot, '.xcodeagent')
    await fs.mkdir(agentDirectory)
    await fs.writeFile(
      path.join(agentDirectory, 'application.json'),
      JSON.stringify({ schemaVersion: 2, appName: '旧版项目' }),
      'utf8'
    )

    await assert.rejects(readManagedWorkspaceApplication(workspaceRoot), /schemaVersion 5/)
  })
})

/** 验证非当前 schemaVersion 即使字段看似完整也不能作为当前工作区读取。 */
test('拒绝非当前 schemaVersion', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    const agentDirectory = path.join(workspaceRoot, '.xcodeagent')
    await fs.mkdir(agentDirectory)
    await fs.writeFile(
      path.join(agentDirectory, 'application.json'),
      JSON.stringify({
        schemaVersion: 4,
        appName: '字段完整项目',
        auth: { enable: true },
        authorization: {
          enabled: true,
          initialAdministratorSubjects: ['ops@example.com']
        }
      }),
      'utf8'
    )

    await assert.rejects(readManagedWorkspaceApplication(workspaceRoot), /schemaVersion 5/)
  })
})

/** 验证权限开启时必须同时提供认证和初始管理员成员。 */
test('拒绝缺少认证或初始管理员的权限工作区', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    const agentDirectory = path.join(workspaceRoot, '.xcodeagent')
    await fs.mkdir(agentDirectory)
    const applicationPath = path.join(agentDirectory, 'application.json')
    const base = {
      schemaVersion: 5,
      appName: '权限项目',
      auth: { enable: false },
      authorization: {
        enabled: true,
        initialAdministratorSubjects: []
      }
    }
    await fs.writeFile(applicationPath, JSON.stringify(base), 'utf8')
    await assert.rejects(readManagedWorkspaceApplication(workspaceRoot), /初始管理员/)

    await fs.writeFile(
      applicationPath,
      JSON.stringify({
        ...base,
        auth: { enable: true },
        authorization: { ...base.authorization, initialAdministratorSubjects: ['ops@example.com'] }
      }),
      'utf8'
    )
    await readManagedWorkspaceApplication(workspaceRoot)
  })
})

/** 验证当前 v5 不接受已删除的权限 provider 或独立运行态页面字段。 */
test('拒绝旧权限字段', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    const agentDirectory = path.join(workspaceRoot, '.xcodeagent')
    await fs.mkdir(agentDirectory)
    await fs.writeFile(
      path.join(agentDirectory, 'application.json'),
      JSON.stringify({
        schemaVersion: 5,
        appName: '旧权限字段项目',
        auth: { enable: false },
        authorization: {
          enabled: false,
          runtimeManagementPageEnabled: false,
          providerMode: 'builtin',
          initialAdministratorSubjects: []
        }
      }),
      'utf8'
    )

    await assert.rejects(readManagedWorkspaceApplication(workspaceRoot), /authorization 配置/)
  })
})

/** 验证关闭权限时不能残留初始管理员种子。 */
test('拒绝关闭权限后残留初始管理员种子', async () => {
  await withTemporaryWorkspace(async (workspaceRoot) => {
    const agentDirectory = path.join(workspaceRoot, '.xcodeagent')
    await fs.mkdir(agentDirectory)
    await fs.writeFile(
      path.join(agentDirectory, 'application.json'),
      JSON.stringify({
        schemaVersion: 5,
        appName: '配置冲突项目',
        auth: { enable: true },
        authorization: {
          enabled: false,
          initialAdministratorSubjects: ['ops@example.com']
        }
      }),
      'utf8'
    )

    await assert.rejects(readManagedWorkspaceApplication(workspaceRoot), /未启用权限时不能保留初始管理员/)
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
