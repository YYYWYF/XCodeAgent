import {
  applicationIndexOf,
  applicationSchemaOf,
  loadStoredApplications,
  loadWorkspaceApplicationConfig,
  saveStoredApplications,
  saveWorkspaceApplicationConfig
} from '../../service/applicationStorage'
import { resolveBranchChain } from '../../service/applicationBranches'
import { encryptApplicationForPersistence } from '../../service/databaseCredentialCrypto'
import type { ApplicationConfig, ApplicationBranch } from '../../typings'

/**
 * 保存工作区唯一 application.json，并更新不含配置副本的首页索引。
 *
 * **分支状态一律以磁盘为准**（`resolveBranchChain`）：本函数的入参是**规划快照**或设置表单，
 * 都不是分支状态的来源 ——
 * - `useApplicationTemplateGeneration` 在模板就绪时把 `planning.application` 写回，而那份
 *   快照是**发起迭代时**登记进规划状态的（早于新分支创建），带的是旧分支表与旧指针；
 * - 设置表单只改应用配置，不碰分支。
 *
 * 用「合并、内存优先」的口径会让快照把指针拉回去：线上出现过 `dev` 已切到 `dev_1`、模板就绪时
 * 被快照写回 `branchName: dev`，顶部从 dev_1 掉回 dev，下拉里 dev 还被标成"当前分支"。
 * 磁盘没有分支表时（新建应用刚写完 application.json）才回退到入参那份。
 *
 * 工作台自己改分支状态走的是 `pages/WorkbenchPage.tsx` 的 `persistApplicationConfig`，
 * 那里用 `mergeBranches`（内存是权威、只能增不能减），两条路径口径不同是刻意的。
 */
export async function saveApplication(application: ApplicationConfig): Promise<ApplicationConfig> {
  const persistedApplication = await encryptApplicationForPersistence(application)
  const workspaceRoot = persistedApplication.workspaceRoot?.trim()
  if (!workspaceRoot) throw new Error('应用缺少工作区路径，不能保存配置')
  const schema = applicationSchemaOf(persistedApplication)
  let chain: { branches?: ApplicationBranch[]; branchName?: string } = {}
  try {
    const disk = await loadWorkspaceApplicationConfig(workspaceRoot)
    chain = resolveBranchChain({
      diskBranches: disk.branches,
      diskBranchName: disk.branchName,
      memoryBranches: schema.branches,
      memoryBranchName: schema.branchName
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
