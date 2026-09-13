import {
  applicationIndexOf,
  applicationSchemaOf,
  loadStoredApplications,
  saveStoredApplications,
  saveWorkspaceApplicationConfig
} from '../../service/applicationStorage'
import { encryptApplicationForPersistence } from '../../service/databaseCredentialCrypto'
import type { ApplicationConfig } from '../../typings'

/** 保存工作区唯一 application.json，并更新不含配置副本的首页索引。 */
export async function saveApplication(application: ApplicationConfig): Promise<ApplicationConfig> {
  const persistedApplication = await encryptApplicationForPersistence(application)
  const workspaceRoot = persistedApplication.workspaceRoot?.trim()
  if (!workspaceRoot) throw new Error('应用缺少工作区路径，不能保存配置')
  const savedSchema = await saveWorkspaceApplicationConfig(
    workspaceRoot,
    applicationSchemaOf(persistedApplication)
  )
  const savedApplication: ApplicationConfig = {
    ...persistedApplication,
    ...savedSchema,
    lastOpenedAt: Date.now()
  }
  const storedApplications = await loadStoredApplications()
  const nextApplications = [
    applicationIndexOf(savedApplication),
    ...storedApplications.filter(
      (storedApplication) => storedApplication.id !== savedApplication.id &&
        storedApplication.workspaceRoot !== savedApplication.workspaceRoot
    ).map(applicationIndexOf)
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
