import fs from 'node:fs/promises'
import path from 'node:path'
import { lstatIfPresent } from './filesystem'

/** 校验 application.json 是否符合当前权限配置契约。 */
export function assertCurrentApplicationSchema(applicationRecord: Record<string, unknown>): void {
  if (applicationRecord.schemaVersion !== 5) {
    throw new Error('无法添加该项目：application.json 必须使用当前 schemaVersion 5')
  }

  const authorization = applicationRecord.authorization
  const authorizationRecord =
    authorization && typeof authorization === 'object' && !Array.isArray(authorization)
      ? (authorization as Record<string, unknown>)
      : undefined
  const administratorSubjectsValue = authorizationRecord?.initialAdministratorSubjects
  const authorizationKeys = authorizationRecord ? Object.keys(authorizationRecord) : []
  if (
    !authorizationRecord ||
    typeof authorizationRecord.enabled !== 'boolean' ||
    authorizationKeys.some((key) => !['enabled', 'initialAdministratorSubjects'].includes(key)) ||
    !Array.isArray(administratorSubjectsValue) ||
    !administratorSubjectsValue.every(
      (subject: unknown) => typeof subject === 'string' && subject.trim().length > 0
    )
  ) {
    throw new Error('无法添加该项目：application.json 缺少有效的 authorization 配置')
  }

  const authorizationEnabled = authorizationRecord.enabled === true
  const administratorSubjects = authorizationRecord.initialAdministratorSubjects as string[]
  if (!authorizationEnabled && administratorSubjects.length > 0) {
    throw new Error('无法添加该项目：未启用权限时不能保留初始管理员')
  }
  if (authorizationEnabled && administratorSubjects.length === 0) {
    throw new Error('无法添加该项目：启用权限后至少需要一个初始管理员成员')
  }
  if (
    authorizationEnabled &&
    administratorSubjects.some((subject) => subject.trim() === 'current-user')
  ) {
    throw new Error('无法添加该项目：初始管理员必须使用真实 subjectId，不能使用 current-user')
  }

  const auth = applicationRecord.auth
  if (
    !auth ||
    typeof auth !== 'object' ||
    Array.isArray(auth) ||
    typeof (auth as Record<string, unknown>).enable !== 'boolean'
  ) {
    throw new Error('无法添加该项目：application.json 缺少有效的 auth 配置')
  }
  if (authorizationEnabled && (auth as Record<string, unknown>).enable !== true) {
    throw new Error('无法添加该项目：启用权限时必须同时启用认证')
  }
}

/** 根据已持久化的权限开关确定前后端模板唯一允许的分支。 */
export function resolveApplicationTemplateBranch(applicationRecord: Record<string, unknown>): 'main' | 'auth' {
  assertCurrentApplicationSchema(applicationRecord)
  const authorization = applicationRecord.authorization as Record<string, unknown>
  return authorization.enabled === true ? 'auth' : 'main'
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
