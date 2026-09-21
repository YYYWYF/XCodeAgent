import {
  applicationIndexOf,
  applicationSchemaOf,
  loadStoredApplications,
  loadWorkspaceApplicationConfig,
  saveStoredApplications,
  saveWorkspaceApplicationConfig
} from '../../service/applicationStorage'
import { mergeVersionChain } from '../../service/applicationVersions'
import { encryptApplicationForPersistence } from '../../service/databaseCredentialCrypto'
import type { ApplicationConfig, ApplicationVersion } from '../../typings'

/**
 * 保存工作区唯一 application.json，并更新不含配置副本的首页索引。
 *
 * **写回前与磁盘合并版本链**：本函数的入参常来自规划状态里持有的应用快照
 * （见 useApplicationTemplateGeneration —— 模板就绪时把 `planning.application` 写回），
 * 而那份快照可能早于本次迭代注册，带的是旧版本链。直接写回去会**静默抹掉新版本**：
 * 线上出现过 v1.1 已创建并写盘、模板就绪时被这份陈旧快照覆盖，界面顶部掉回 v1.0。
 */
export async function saveApplication(application: ApplicationConfig): Promise<ApplicationConfig> {
  const persistedApplication = await encryptApplicationForPersistence(application)
  const workspaceRoot = persistedApplication.workspaceRoot?.trim()
  if (!workspaceRoot) throw new Error('应用缺少工作区路径，不能保存配置')
  const schema = applicationSchemaOf(persistedApplication)
  let chain: { versions?: ApplicationVersion[]; currentVersionId?: string } = {}
  try {
    const disk = await loadWorkspaceApplicationConfig(workspaceRoot)
    chain = mergeVersionChain({
      memoryVersions: schema.versions,
      diskVersions: disk.versions,
      memoryCurrentVersionId: schema.currentVersionId,
      diskCurrentVersionId: disk.currentVersionId
    })
  } catch (error) {
    // 读不到磁盘配置时按原样写回：不能因为合并失败就阻断保存。
    console.warn('读取磁盘 application.json 失败，按内存状态写回。', error)
  }
  const savedSchema = await saveWorkspaceApplicationConfig(workspaceRoot, {
    ...schema,
    ...chain
  })
  const savedApplication: ApplicationConfig = {
    ...persistedApplication,
    ...savedSchema,
    lastOpenedAt: Date.now()
  }
  const storedApplications = await loadStoredApplications()
  const nextApplications = [
    applicationIndexOf(savedApplication),
    ...storedApplications
      .filter(
        (storedApplication) =>
          storedApplication.id !== savedApplication.id &&
          storedApplication.workspaceRoot !== savedApplication.workspaceRoot
      )
      .map(applicationIndexOf)
  ]
  await saveStoredApplications(nextApplications)
  return savedApplication
}

/** 保存应用后使用同一个密文对象打开工作台。 */
export async function saveAndOpenApplication(
  application: ApplicationConfig,
  onOpenApplication: (application: ApplicationConfig) => void
): Promise<void> {
  const persistedApplication = await saveApplication(application)
  onOpenApplication(persistedApplication)
}
