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
