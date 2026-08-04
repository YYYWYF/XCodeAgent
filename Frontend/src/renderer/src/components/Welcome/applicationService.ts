import { loadStoredApplications, saveStoredApplications } from '../../service/applicationStorage'
import { encryptApplicationForPersistence } from '../../service/databaseCredentialCrypto'
import type { ApplicationConfig } from '../../typings'

/** 保存不含 plantMode 明文密码的应用索引，并返回实际持久化对象。 */
export async function saveApplication(application: ApplicationConfig): Promise<ApplicationConfig> {
  const persistedApplication = await encryptApplicationForPersistence(application)
  const storedApplications = await loadStoredApplications()
  const nextApplications = [
    persistedApplication,
    ...storedApplications.filter(
      (storedApplication) =>
        storedApplication.id !== persistedApplication.id &&
        (!persistedApplication.workspaceRoot ||
          storedApplication.workspaceRoot !== persistedApplication.workspaceRoot)
    )
  ]
  await saveStoredApplications(nextApplications)
  return persistedApplication
}

/** 保存应用后使用同一个密文对象打开工作台。 */
export async function saveAndOpenApplication(
  application: ApplicationConfig,
  onOpenApplication: (application: ApplicationConfig) => void
): Promise<void> {
  const persistedApplication = await saveApplication(application)
  onOpenApplication(persistedApplication)
}
