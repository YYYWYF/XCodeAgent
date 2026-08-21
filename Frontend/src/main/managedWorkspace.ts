import fs from 'node:fs/promises'
import path from 'node:path'
import { lstatIfPresent } from './filesystem'

/** 校验 application.json 是否符合当前权限配置契约。 */
export function assertCurrentApplicationSchema(applicationRecord: Record<string, unknown>): void {
  if (applicationRecord.schemaVersion !== 3) {
    throw new Error('无法添加该项目：仅支持 schemaVersion 为 3 的 application.json')
  }
  const authorization = applicationRecord.authorization
  if (
    !authorization ||
    typeof authorization !== 'object' ||
    Array.isArray(authorization) ||
    typeof (authorization as Record<string, unknown>).enabled !== 'boolean' ||
    typeof (authorization as Record<string, unknown>).runtimeManagementPageEnabled !== 'boolean'
  ) {
    throw new Error('无法添加该项目：application.json 缺少有效的 authorization 配置')
  }
}

/** 校验并读取受 XCodeAgent 管理的工作区配置，拒绝缺少真实 .xcodeagent 目录的文件夹。 */
export async function readManagedWorkspaceApplication(
  workspaceRoot: string
): Promise<Record<string, unknown>> {
  const agentDirectory = path.join(workspaceRoot, '.xcodeagent')
  const agentStats = await lstatIfPresent(agentDirectory)
  if (!agentStats) {
    throw new Error('所选文件夹不是 XCodeAgent 项目：缺少 .xcodeagent 目录')
  }
  if (!agentStats.isDirectory() || agentStats.isSymbolicLink()) {
    throw new Error('所选文件夹的 .xcodeagent 必须是真实目录，不能是文件或符号链接')
  }

  let applicationConfig: unknown
  try {
    const rawValue = await fs.readFile(path.join(agentDirectory, 'application.json'), 'utf8')
    applicationConfig = JSON.parse(rawValue || '{}')
  } catch (error) {
    const reason = error instanceof SyntaxError ? '内容格式无效' : '文件不存在或无法读取'
    throw new Error(`无法添加该项目：.xcodeagent/application.json ${reason}`)
  }

  if (
    !applicationConfig ||
    typeof applicationConfig !== 'object' ||
    Array.isArray(applicationConfig)
  ) {
    throw new Error('无法添加该项目：.xcodeagent/application.json 必须是对象')
  }

  const applicationRecord = applicationConfig as Record<string, unknown>
  if (typeof applicationRecord.appName !== 'string' || !applicationRecord.appName.trim()) {
    throw new Error('无法添加该项目：.xcodeagent/application.json 缺少有效的 appName')
  }
  assertCurrentApplicationSchema(applicationRecord)

  return applicationRecord
}
