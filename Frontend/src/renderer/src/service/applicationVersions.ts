import type { ApplicationConfig, ApplicationLifecycle, ApplicationVersion } from '../typings'

/**
 * 应用版本帮助函数。
 * 版本模型：单线里程碑 —— versions 是链式只读归档，无分叉。
 * 当前版本（currentVersionId 指向）= 用户正在改的迭代；已发布版本 = 锁定只读历史。
 */

/** 取当前版本（currentVersionId 指向）。 */
export function currentVersion(app: ApplicationConfig): ApplicationVersion | undefined {
  if (!app.versions || !app.currentVersionId) return undefined
  return app.versions.find((v) => v.id === app.currentVersionId)
}

/** 已发布里程碑列表（按时间正序）。 */
export function releasedVersions(app: ApplicationConfig): ApplicationVersion[] {
  return (app.versions || []).filter((v) => v.status === 'released')
}

/** 当前迭代版本（当前版本且状态为 iterating）。无当前迭代时返回 undefined。 */
export function iteratingVersion(app: ApplicationConfig): ApplicationVersion | undefined {
  const v = currentVersion(app)
  return v && v.status === 'iterating' ? v : undefined
}

/** 按 id 查版本。 */
export function findVersion(
  app: ApplicationConfig,
  versionId: string
): ApplicationVersion | undefined {
  return (app.versions || []).find((v) => v.id === versionId)
}

/** 版本是否可编辑（只有 iterating 状态可改）。 */
export function isVersionEditable(v?: ApplicationVersion): boolean {
  return Boolean(v && v.status === 'iterating')
}

/**
 * 是否正在回看**历史版本**（非活跃版本）。
 *
 * 工作台内容区据此在"对话区执行情况视图"与"只读的应用文件/应用预览"之间切换。
 * 刻意不看版本状态：当前版本发布后也是 released、阶段同样不可点，但它仍是用户正在
 * 推进的应用，必须保留对话区；只有切到非活跃版本才是回看。用"阶段锁定"判断会把
 * 刚发布的当前版本也换成只读视图。
 */
export function isViewingHistoricalVersion(
  activeVersionId: string,
  viewedVersionId: string | undefined
): boolean {
  return Boolean(viewedVersionId) && viewedVersionId !== activeVersionId
}

/** 生成版本稳定 id。 */
export function createApplicationVersionId(
  applicationId: string,
  major: number,
  minor: number
): string {
  return `${applicationId}-v${major}-${minor}`
}

/** 拼版本号标签。 */
export function formatVersionLabel(major: number, minor: number): string {
  return `v${major}.${minor}`
}

/**
 * 从用户在新建表单填写的版本号标签解析 major.minor。
 * 形如 "v1.0" / "1.0" / "v1" 均解析为 { major:1, minor:0 }；无法解析时回退 1.0。
 */
export function parseVersionLabel(label: string): { major: number; minor: number } {
  const cleaned = String(label || '')
    .trim()
    .replace(/^v/i, '')
  const match = /^(\d+)(?:\.(\d+))?$/.exec(cleaned)
  if (!match) return { major: 1, minor: 0 }
  const major = Math.max(0, parseInt(match[1], 10) || 1)
  const minor = match[2] !== undefined ? Math.max(0, parseInt(match[2], 10) || 0) : 0
  return { major, minor }
}

/** 基于父版本递增版本号（minor 递增；major 留接口不做）。 */
export function bumpVersionLabel(parent: { major: number; minor: number }): {
  major: number
  minor: number
  versionLabel: string
} {
  const major = parent.major
  const minor = parent.minor + 1
  return { major, minor, versionLabel: formatVersionLabel(major, minor) }
}

/**
 * 可生成版本判定：当前迭代必须同时完成全部用例、通过审查和用户明确验收。
 * 验收未通过前不得把审查完成态直接视为可发布，避免绕过用户确认门禁。
 */
export function isVersionReleasable(v?: ApplicationVersion): boolean {
  if (!v || v.status !== 'iterating') return false
  const lifecycle = v.lifecycle
  const extensions = (lifecycle?.extensions || {}) as Record<string, unknown>
  const testExecutionStatus = String(extensions.testExecutionStatus || '')
  const testExecutionPassed = testExecutionStatus === 'passed'
  const reviewCompleted =
    String(extensions.reviewStatus || '') === 'passed' ||
    Object.values(lifecycle?.activeExecutions || {}).some(
      (execution) =>
        (execution as { phase?: string; status?: string }).phase === 'code_review' &&
        (execution as { status?: string }).status === 'completed'
    )
  const acceptancePassed = String(extensions.acceptanceStatus || '') === 'passed'
  return testExecutionPassed && reviewCompleted && acceptancePassed
}

/**
 * 新建应用时产生首个迭代版本（iterating）。
 * 版本标签取自用户在表单填写的 versionNo，解析为 major.minor。
 */
export function createInitialVersion(
  applicationId: string,
  versionNo: string,
  lifecycle: ApplicationLifecycle,
  now: number
): ApplicationVersion {
  const { major, minor } = parseVersionLabel(versionNo)
  return {
    id: createApplicationVersionId(applicationId, major, minor),
    versionLabel: formatVersionLabel(major, minor),
    major,
    minor,
    status: 'iterating',
    createdAt: now,
    lifecycle
  }
}

/**
 * 基于父版本派生新迭代版本（iterating，lifecycle 由调用方传入 —— 通常是重置为 collecting_requirement）。
 */
export function createIterationVersion(
  applicationId: string,
  parent: ApplicationVersion,
  lifecycle: ApplicationLifecycle,
  now: number
): ApplicationVersion {
  const { major, minor, versionLabel } = bumpVersionLabel(parent)
  return {
    id: createApplicationVersionId(applicationId, major, minor),
    versionLabel,
    major,
    minor,
    status: 'iterating',
    parentVersionId: parent.id,
    createdAt: now,
    lifecycle
  }
}

/** 把历史版本内容恢复为新的顺序版本，保留单向版本链和完整回退记录。 */
export function createRollbackVersion(
  applicationId: string,
  currentHead: ApplicationVersion,
  restoredVersion: ApplicationVersion,
  now: number
): ApplicationVersion {
  const { major, minor, versionLabel } = bumpVersionLabel(currentHead)
  return {
    id: createApplicationVersionId(applicationId, major, minor),
    versionLabel,
    major,
    minor,
    status: 'iterating',
    parentVersionId: currentHead.id,
    restoredFromVersionId: restoredVersion.id,
    createdAt: now,
    lifecycle: restoredVersion.lifecycle,
    snapshot: restoredVersion.snapshot
  }
}

/**
 * 把指定迭代版本标记为已发布（released）：写发布时间、版本日志与 Git 提交/Tag 引用。
 * gitRef 由后端真实 push 返回；未传时回退到模拟值（保留兼容，仅未接后端的场景使用）。
 */
export function releaseVersion(
  version: ApplicationVersion,
  description: string,
  now: number,
  gitRef?: { commitSha: string; tag: string; committedAt: number }
): ApplicationVersion {
  const tag = gitRef?.tag || version.versionLabel
  return {
    ...version,
    status: 'released',
    releasedAt: now,
    description,
    gitRef: gitRef || {
      commitSha: '',
      tag,
      committedAt: now
    }
  }
}

/**
 * 解析当前版本指针，强制"指针指向正在编辑的那一轮迭代"这一不变式。
 *
 * `currentVersionId` 的语义是**当前迭代版本**（见 ApplicationVersion 的类型说明），
 * 而"已发布"是历史。所以只要有 `iterating` 版本，指针就必须指向**最后一个** iterating ——
 * 陈旧快照（如发起迭代前注册进规划状态的那份应用配置）会把指针留在上一轮已发布的
 * 版本上，界面顶部就会显示旧版本号，而实际在编辑的是新版本。
 *
 * 线上出现过：v1.1 已创建，模板就绪时一份旧快照写回，versions 数组还在（合并保住了）
 * 但指针被拉回 v1.0，顶部版本号从 v1.1 掉成 v1.0。
 *
 * 没有 iterating 版本时（刚发布、尚未发起迭代）保留传入的指针，只要它在链上有效。
 */
export function resolveCurrentVersionId(
  versions: ApplicationVersion[],
  preferred?: string
): string | undefined {
  if (versions.length === 0) return undefined
  const latestIterating = versions.filter((version) => version.status === 'iterating').at(-1)
  if (latestIterating) return latestIterating.id
  if (preferred && versions.some((version) => version.id === preferred)) return preferred
  return versions.at(-1)?.id
}

/**
 * 决定版本链以谁为准：磁盘上的 application.json，还是内存里的当前状态。
 *
 * 磁盘是权威来源 —— 它由发布/迭代流程经 `saveWorkspaceApplicationConfig` 写入，
 * 刷新后仍然存在。内存那份可能是组件挂载时初始化的占位（例如刚造出的 `[v1.0]`）。
 *
 * 曾经这里无条件取内存那份，导致从首页打开一个已发布多个版本的应用时，磁盘上的
 * v1.0/v1.1/v1.2 被内存里刚初始化的 `[v1.0]` 冲掉：版本选择器只剩 v1.0，
 * 下拉列表里看不到其它版本。回退到内存只应发生在**磁盘确实没有版本链**时
 * （旧数据兼容：application.json 尚无 versions 字段）。
 */
export function resolveVersionChain(input: {
  diskVersions?: ApplicationVersion[]
  diskCurrentVersionId?: string
  memoryVersions?: ApplicationVersion[]
  memoryCurrentVersionId?: string
}): { versions?: ApplicationVersion[]; currentVersionId?: string } {
  const diskVersions = Array.isArray(input.diskVersions) ? input.diskVersions : []
  if (diskVersions.length > 0) {
    return {
      versions: diskVersions,
      // 同一不变式：磁盘指针也可能陈旧（见 resolveCurrentVersionId）。
      currentVersionId: resolveCurrentVersionId(
        diskVersions,
        (typeof input.diskCurrentVersionId === 'string' && input.diskCurrentVersionId) ||
          input.memoryCurrentVersionId
      )
    }
  }
  const memoryVersions = input.memoryVersions ?? []
  return {
    versions: memoryVersions,
    currentVersionId: resolveCurrentVersionId(memoryVersions, input.memoryCurrentVersionId)
  }
}

/**
 * 写回 application.json 前，把内存里的版本链与磁盘上的合并。
 *
 * **内存状态可能陈旧于磁盘**：发起迭代 / 发布 / 回退都是先改内存再写盘，期间若有别的
 * 写入（或状态未及时同步），直接把内存那份写回去会**静默抹掉磁盘上的版本**。
 * 线上出现过：v1.1 已创建并写盘，随后一次写回把 application.json 退回成只有 v1.0，
 * 界面顶部只剩 v1.0、下拉里没有 v1.1 —— 而磁盘上 v1.1 的提交记录还在。
 *
 * 规则：磁盘是权威，内存只能**推进**、不能减少。
 * - 磁盘有、内存没有的版本：补回来（内存落后了）
 * - 两边都有：以内存为准（它刚被本次操作更新过，例如发布把 v1.0 转成 released）
 * - 内存有、磁盘没有：保留（本次新建的版本）
 *
 * 顺序按磁盘的既有顺序排列，新增的追加在末尾，保持"单线只读归档"的时间正序。
 */
export function mergeVersionChain(input: {
  memoryVersions?: ApplicationVersion[]
  diskVersions?: ApplicationVersion[]
  memoryCurrentVersionId?: string
  diskCurrentVersionId?: string
}): { versions: ApplicationVersion[]; currentVersionId?: string } {
  const memory = Array.isArray(input.memoryVersions) ? input.memoryVersions : []
  const disk = Array.isArray(input.diskVersions) ? input.diskVersions : []

  const memoryById = new Map(memory.map((version) => [version.id, version]))
  const diskIds = new Set(disk.map((version) => version.id))

  const merged: ApplicationVersion[] = disk.map((version) => memoryById.get(version.id) ?? version)
  for (const version of memory) {
    if (!diskIds.has(version.id)) merged.push(version)
  }

  // 指针按不变式解析：有 iterating 版本时必须指向它，不能被陈旧快照拉回已发布版本。
  // 内存的指针优先于磁盘的（它反映本次操作的结果），但两者都要过这一层校验。
  return {
    versions: merged,
    currentVersionId: resolveCurrentVersionId(
      merged,
      input.memoryCurrentVersionId || input.diskCurrentVersionId
    )
  }
}
