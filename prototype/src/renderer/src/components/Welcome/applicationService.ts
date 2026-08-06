import { loadStoredApplications, saveStoredApplications } from '../../service/applicationStorage'
import type { ApplicationConfig } from '../../typings'

export async function saveApplication(application: ApplicationConfig) {
  const storedApplications = await loadStoredApplications()
  const nextApplications = [
    application,
    ...storedApplications.filter(
      (storedApplication) =>
        storedApplication.id !== application.id &&
        (!application.workspaceRoot ||
          storedApplication.workspaceRoot !== application.workspaceRoot)
    )
  ]
  await saveStoredApplications(nextApplications)
}

export async function saveAndOpenApplication(
  application: ApplicationConfig,
  onOpenApplication: (application: ApplicationConfig) => void
) {
  await saveApplication(application)
  onOpenApplication(application)
}
