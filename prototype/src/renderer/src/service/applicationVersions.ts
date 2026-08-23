import type { ApplicationConfig, ApplicationLifecycle, ApplicationVersion } from '../typings'

/**
 * 应用版本帮助函数。
 * 版本模型:单线里程碑 —— versions 是链式只读归档,无分叉。
 * 当前版本(currentVersionId 指向)= 用户正在改的迭代;已发布版本 = 锁定只读历史。
 */

/** 取当前版本(currentVersionId 指向)。 */
export function currentVersion(app: ApplicationConfig): ApplicationVersion | undefined {
  if (!app.versions || !app.currentVersionId) return undefined
  return app.versions.find((v) => v.id === app.currentVersionId)
}

/** 已发布里程碑列表(按时间正序)。 */
export function releasedVersions(app: ApplicationConfig): ApplicationVersion[] {
  return (app.versions || []).filter((v) => v.status === 'released')
}

/** 当前迭代版本(当前版本且状态为 iterating)。无当前迭代时返回 undefined。 */
export function iteratingVersion(app: ApplicationConfig): ApplicationVersion | undefined {
  const v = currentVersion(app)
  return v && v.status === 'iterating' ? v : undefined
}

/** 按 id 查版本。 */
export function findVersion(app: ApplicationConfig, versionId: string): ApplicationVersion | undefined {
  return (app.versions || []).find((v) => v.id === versionId)
}

/** 版本是否可编辑(只有 iterating 状态可改)。 */
export function isVersionEditable(v?: ApplicationVersion): boolean {
  return Boolean(v && v.status === 'iterating')
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

/** 基于父版本递增版本号(minor 递增;major 留接口不做)。 */
export function bumpVersionLabel(parent: {
  major: number
  minor: number
}): { major: number; minor: number; versionLabel: string } {
  const major = parent.major
  const minor = parent.minor + 1
  return { major, minor, versionLabel: formatVersionLabel(major, minor) }
}

/**
 * 可生成版本判定：当前迭代必须同时拥有当前 revision 的合格测试报告和通过的审查。
 * 只看 finalize_project 会让旧的审查完成态绕过测试门禁，因此测试结论是必要条件。
 */
export function isVersionReleasable(v?: ApplicationVersion): boolean {
  if (!v || v.status !== 'iterating') return false
  const lifecycle = v.lifecycle
  const testReportStatus = String(lifecycle?.extensions?.testReportStatus || '')
  const testReportPassed = ['passed', 'qualified', '合格'].includes(testReportStatus)
  const reviewCompleted = Object.values(lifecycle?.activeExecutions || {}).some(
    (execution) =>
      execution.phase === 'finalize_project' && execution.status === 'completed'
  )
  return testReportPassed && reviewCompleted
}

/** 新建应用时产生初始 v1.0(迭代中)。 */
export function createInitialVersion(
  applicationId: string,
  lifecycle: ApplicationLifecycle,
  now: number
): ApplicationVersion {
  return {
    id: createApplicationVersionId(applicationId, 1, 0),
    versionLabel: formatVersionLabel(1, 0),
    major: 1,
    minor: 0,
    status: 'iterating',
    createdAt: now,
    lifecycle
  }
}

/**
 * 基于父版本派生新迭代版本(迭代中,lifecycle 由调用方传入 —— 通常是重置为 collecting_requirement)。
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
