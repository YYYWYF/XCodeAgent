import type { ApplicationConfig, ApplicationBranch, ApplicationLifecycle } from '../typings'

/**
 * 应用分支帮助函数。
 * 分支模型：应用有多条分支，`branchName` 指向当前正在开发的那条。
 * 当前分支可编辑；其余分支只读回看（历史）。
 */

/** 取当前分支记录（`branchName` 指向）。 */
export function currentBranch(app: ApplicationConfig): ApplicationBranch | undefined {
  if (!app.branches || !app.branchName) return undefined
  return app.branches.find((branch) => branch.name === app.branchName)
}

/** 按分支名查记录。 */
export function findBranch(
  app: ApplicationConfig,
  branchName: string | undefined
): ApplicationBranch | undefined {
  if (!branchName) return undefined
  return (app.branches || []).find((branch) => branch.name === branchName)
}

/** 当前正在开发的分支名（未配置时回退应用 id，保证作用域键非空）。 */
export function activeBranchName(app: ApplicationConfig): string {
  return app.branchName || app.id
}

/**
 * 是否正在回看**历史分支**（非当前分支）。
 *
 * 工作台内容区据此在"对话区执行情况视图"与"只读的应用文件/应用分支预览"之间切换。
 * 分支模型下不再需要看版本状态：当前分支就是可编辑的那条，其余一律只读。
 */
export function isViewingHistoricalBranch(
  activeBranch: string,
  viewedBranch: string | undefined
): boolean {
  return Boolean(viewedBranch) && viewedBranch !== activeBranch
}

/**
 * 可提交推送判定：当前分支必须同时完成全部用例、通过审查和用户明确验收。
 * 验收未通过前不得把审查完成态直接视为可提交，避免绕过用户确认门禁。
 */
export function isBranchPublishable(lifecycle?: ApplicationLifecycle): boolean {
  if (!lifecycle) return false
  const extensions = (lifecycle.extensions || {}) as Record<string, unknown>
  const testExecutionPassed = String(extensions.testExecutionStatus || '') === 'passed'
  const reviewCompleted =
    String(extensions.reviewStatus || '') === 'passed' ||
    Object.values(lifecycle.activeExecutions || {}).some(
      (execution) =>
        (execution as { phase?: string; status?: string }).phase === 'code_review' &&
        (execution as { status?: string }).status === 'completed'
    )
  const acceptancePassed = String(extensions.acceptanceStatus || '') === 'passed'
  return testExecutionPassed && reviewCompleted && acceptancePassed
}

/**
 * 迭代作用域键：标识"哪条分支的第几轮迭代"。
 *
 * 同一条分支上可以继续迭代多轮，所以分支名单独不足以标识一轮 —— 必须带上**迭代令牌**
 * （lifecycle 的 threadId，每次发起迭代都是新的 randomUUID）。
 *
 * 用途之一是阶段 Provider 的 React key：只用分支名的话，在当前分支继续迭代时 key 不变、
 * Provider 不会重挂载，它会一直沿用内存里上一轮的手动阶段覆盖（例如"验收"），于是发起
 * 新迭代后界面仍停在验收阶段。旧模型下版本 id 每次迭代都变，这个重挂载是"免费"的。
 */
export function branchIterationScope(
  branchName: string,
  lifecycle?: ApplicationLifecycle
): string {
  const token = String(lifecycle?.initialization?.threadId || '')
  return token ? `${branchName}:${token}` : branchName
}

/** 新建应用时产生首条分支记录。 */export function createInitialBranch(
  branchName: string,
  lifecycle: ApplicationLifecycle,
  now: number
): ApplicationBranch {
  return { name: branchName, createdAt: now, lifecycle }
}

/**
 * 发起新迭代选择「新建分支」时追加的分支记录。
 *
 * lifecycle 由调用方传入（通常是重置后的 collecting_requirement）。
 */
export function createBranchRecord(
  branchName: string,
  lifecycle: ApplicationLifecycle,
  now: number
): ApplicationBranch {
  return { name: branchName, createdAt: now, lifecycle }
}

/**
 * 解析当前分支名，强制"指针指向 branches 里真实存在的那条"这一不变式。
 *
 * 陈旧快照（如发起迭代前注册进规划状态的那份应用配置）会把指针留在上一条分支上，
 * 界面顶部就会显示旧分支名，而实际在编辑的是新分支。
 *
 * 传入的分支名不在表里时退回最后一条（最近创建的分支）。
 *
 * **例外：分支表还空着时必须保留传入的分支名。** 新建应用时表单只写了 `branchName`，
 * 首条分支记录要等进入工作台才建（它需要 lifecycle，那时才拿得到）。此时指针是分支名的
 * 唯一来源，丢掉它会让远端分支创建整段被跳过（"当前应用未配置分支名"就是这么来的）。
 */
export function resolveCurrentBranchName(
  branches: ApplicationBranch[],
  preferred?: string
): string | undefined {
  const wanted = (preferred ?? '').trim()
  if (branches.length === 0) return wanted || undefined
  if (wanted && branches.some((branch) => branch.name === wanted)) return wanted
  return branches.at(-1)?.name
}

/**
 * 决定分支表以谁为准：磁盘上的 application.json，还是内存里的当前状态。
 *
 * 磁盘是权威来源 —— 它由提交/迭代流程经 `saveWorkspaceApplicationConfig` 写入，
 * 刷新后仍然存在。内存那份可能是组件挂载时初始化的占位（例如刚造出的 `[dev]`）。
 *
 * 曾经这里无条件取内存那份，导致从首页打开一个已发布多个版本的应用时，磁盘上的
 * 版本链被内存里刚初始化的占位冲掉：版本选择器只剩一条。回退到内存只应发生在
 * **磁盘确实没有分支表**时。
 */
export function resolveBranchChain(input: {
  diskBranches?: ApplicationBranch[]
  diskBranchName?: string
  memoryBranches?: ApplicationBranch[]
  memoryBranchName?: string
}): { branches?: ApplicationBranch[]; branchName?: string } {
  const diskBranches = Array.isArray(input.diskBranches) ? input.diskBranches : []
  if (diskBranches.length > 0) {
    return {
      branches: diskBranches,
      // 同一不变式：磁盘指针也可能陈旧（见 resolveCurrentBranchName）。
      branchName: resolveCurrentBranchName(
        diskBranches,
        (typeof input.diskBranchName === 'string' && input.diskBranchName) ||
          input.memoryBranchName
      )
    }
  }
  const memoryBranches = input.memoryBranches ?? []
  return {
    branches: memoryBranches,
    branchName: resolveCurrentBranchName(memoryBranches, input.memoryBranchName)
  }
}

/**
 * 写回 application.json 前，把内存里的分支表与磁盘上的合并。
 *
 * **内存状态可能陈旧于磁盘**：发起迭代 / 提交推送都是先改内存再写盘，期间若有别的
 * 写入（或状态未及时同步），直接把内存那份写回去会**静默抹掉磁盘上的分支**。
 *
 * 规则：磁盘是权威，内存只能**推进**、不能减少。
 * - 磁盘有、内存没有的分支：补回来（内存落后了）
 * - 两边都有：以内存为准（它刚被本次操作更新过，例如提交推送写了新的 gitRef）
 * - 内存有、磁盘没有：保留（本次新建的分支）
 *
 * 顺序按磁盘的既有顺序排列，新增的追加在末尾，保持"按创建时间正序"。
 */
export function mergeBranches(input: {
  memoryBranches?: ApplicationBranch[]
  diskBranches?: ApplicationBranch[]
  memoryBranchName?: string
  diskBranchName?: string
}): { branches: ApplicationBranch[]; branchName?: string } {
  const memory = Array.isArray(input.memoryBranches) ? input.memoryBranches : []
  const disk = Array.isArray(input.diskBranches) ? input.diskBranches : []

  const memoryByName = new Map(memory.map((branch) => [branch.name, branch]))
  const diskNames = new Set(disk.map((branch) => branch.name))

  const merged: ApplicationBranch[] = disk.map(
    (branch) => memoryByName.get(branch.name) ?? branch
  )
  for (const branch of memory) {
    if (!diskNames.has(branch.name)) merged.push(branch)
  }

  // 指针按不变式解析：必须指向合并后真实存在的分支。
  // 内存的指针优先于磁盘的（它反映本次操作的结果），但两者都要过这一层校验。
  return {
    branches: merged,
    branchName: resolveCurrentBranchName(
      merged,
      input.memoryBranchName || input.diskBranchName
    )
  }
}
