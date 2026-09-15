import { loadStoredApplications, saveStoredApplications } from '../../service/applicationStorage'
import type { ApplicationConfig } from '../../typings'

// 工作区路径按 Windows 语义比较：统一分隔符并忽略大小写，与 mock 层
// createProjectDirectory 的占用拦截（normalizeWorkspacePath）保持同一口径，
// 否则仅大小写不同的路径会绕过索引去重、产生同目录双应用。
function sameWorkspacePath(left?: string, right?: string): boolean {
  const normalize = (path?: string): string =>
    String(path || '').trim().replace(/[\\/]+/g, '\\').toLowerCase()
  const a = normalize(left)
  const b = normalize(right)
  return a !== '' && a === b
}

export async function saveApplication(application: ApplicationConfig) {
  const storedApplications = await loadStoredApplications()
  const nextApplications = [
    application,
    ...storedApplications.filter(
      (storedApplication) =>
        storedApplication.id !== application.id &&
        !sameWorkspacePath(storedApplication.workspaceRoot, application.workspaceRoot)
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
