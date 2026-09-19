import type { ApplicationConfig } from './typings'
import { personalizePlanningSeed, synchronizePlanningReferences } from './planning/seed'
import type { InitializationPlanningRecord, InitializationPlanningSeed } from './planning/model'
export * from './planning/model'
export { transitionInitializationPlanning } from './planning/state'

const STORAGE_PREFIX = 'aistudio:prototype:initialization-planning:'
const CHANGE_EVENT = 'aistudio:prototype:initialization-planning-change'

/** 精确按应用和版本定位当前记录，不跨版本比较 revision。 */
function storageKey(application: Pick<ApplicationConfig, 'id' | 'currentVersionId'>): string {
  return `${STORAGE_PREFIX}${application.id}:${application.currentVersionId || 'current'}`
}

/** 从所选版本基线创建正式产物；新迭代继承内容但重新经过全部确认门。 */
export function createInitializationPlanningRecord(application: ApplicationConfig, seed: InitializationPlanningSeed): InitializationPlanningRecord {
  const version = application.versions?.find((item) => item.id === application.currentVersionId)
  const sourceId = version?.restoredFromVersionId || version?.parentVersionId
  const sourceVersion = application.versions?.find((item) => item.id === sourceId)
  const inherited = version?.snapshot?.planning || sourceVersion?.snapshot?.planning ||
    (sourceId ? readInitializationPlanningRecord({ ...application, currentVersionId: sourceId }) : undefined)
  const completed = version?.status === 'released' || version?.lifecycle?.initialization?.stage === 'ready_for_workbench'
  const artifacts = inherited ? structuredClone(inherited.artifacts) : personalizePlanningSeed(application, seed)
  synchronizePlanningReferences(artifacts)
  artifacts.uiDesigns.confirmation_status = completed ? 'confirmed' : 'pending_user_confirmation'
  artifacts.uiDesigns.pages.forEach((page) => { page.status = completed ? 'confirmed' : 'queued' })
  return {
    applicationId: application.id, applicationName: application.name,
    versionId: application.currentVersionId || 'current', workspaceRoot: application.workspaceRoot || '',
    revision: 1, stage: completed ? 'ready_for_workbench' : 'collecting_requirement',
    artifactStatus: {
      'requirement-spec': completed ? 'confirmed' : 'draft', 'product-plan': completed ? 'confirmed' : 'draft',
      'ui-designs': completed ? 'confirmed' : 'draft', 'technical-plan': completed ? 'confirmed' : 'draft'
    },
    artifacts, documents: completed && inherited ? structuredClone(inherited.documents) : {},
    clarificationAnswers: {}, feedback: [], operationStatus: 'idle', updatedAt: new Date().toISOString()
  }
}

/** 仅读取当前契约的精确版本记录，损坏内容不进入工作台。 */
export function readInitializationPlanningRecord(application: Pick<ApplicationConfig, 'id' | 'currentVersionId'>): InitializationPlanningRecord | undefined {
  if (typeof window === 'undefined') return undefined
  try {
    const raw = window.localStorage.getItem(storageKey(application))
    if (!raw) return undefined
    const value = JSON.parse(raw) as InitializationPlanningRecord
    return value.applicationId === application.id && value.versionId === (application.currentVersionId || 'current') &&
      value.documents && value.artifacts && value.artifactStatus && Array.isArray(value.feedback) ? value : undefined
  } catch { return undefined }
}

/** 在生命周期冷启动缺少版本号时读取该应用最近一次记录；正式工作台仍按精确版本读取。 */
export function readLatestInitializationPlanningRecord(applicationId: string): InitializationPlanningRecord | undefined {
  if (typeof window === 'undefined' || !applicationId) return undefined
  const records = Object.keys(window.localStorage)
    .filter((key) => key.startsWith(`${STORAGE_PREFIX}${applicationId}:`))
    .map((key) => {
      try { return JSON.parse(window.localStorage.getItem(key) || '') as InitializationPlanningRecord } catch { return undefined }
    })
    .filter((item): item is InitializationPlanningRecord => Boolean(item?.applicationId === applicationId && item.documents && item.artifacts))
  return records.sort((left, right) => Date.parse(right.updatedAt) - Date.parse(left.updatedAt))[0]
}

/** 按工作区恢复规划记录，供应用索引保留版本标识但生命周期读取缺少旧线程信息时校准。 */
export function readInitializationPlanningRecordByWorkspace(workspaceRoot: string): InitializationPlanningRecord | undefined {
  if (typeof window === 'undefined' || !workspaceRoot) return undefined
  const records = Object.keys(window.localStorage)
    .filter((key) => key.startsWith(STORAGE_PREFIX))
    .map((key) => {
      try { return JSON.parse(window.localStorage.getItem(key) || '') as InitializationPlanningRecord } catch { return undefined }
    })
    .filter((item): item is InitializationPlanningRecord => Boolean(item?.workspaceRoot === workspaceRoot && item.documents && item.artifacts))
  return records.sort((left, right) => Date.parse(right.updatedAt) - Date.parse(left.updatedAt))[0]
}

/** 首次进入注入种子，之后统一读取当前版本的已持久化事实。 */
export function ensureInitializationPlanningRecord(application: ApplicationConfig, seed: InitializationPlanningSeed): InitializationPlanningRecord {
  return readInitializationPlanningRecord(application) || persistInitializationPlanningRecord(createInitializationPlanningRecord(application, seed), application)
}

/** 原子保存完整版本记录并通知当前页面的订阅者。 */
export function persistInitializationPlanningRecord(record: InitializationPlanningRecord, application: Pick<ApplicationConfig, 'id' | 'currentVersionId'>): InitializationPlanningRecord {
  if (record.applicationId !== application.id || record.versionId !== (application.currentVersionId || 'current')) throw new Error('产物记录与当前应用版本不一致。')
  if (typeof window !== 'undefined') {
    window.localStorage.setItem(storageKey(application), JSON.stringify(record))
    window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: { applicationId: record.applicationId, versionId: record.versionId } }))
  }
  return record
}

/** 订阅同页动作与其他浏览器标签的持久化更新。 */
export function subscribeInitializationPlanning(listener: () => void): () => void {
  if (typeof window === 'undefined') return () => undefined
  window.addEventListener(CHANGE_EVENT, listener)
  window.addEventListener('storage', listener)
  return () => { window.removeEventListener(CHANGE_EVENT, listener); window.removeEventListener('storage', listener) }
}

/** 清除指定应用当前版本的规划记录，并通知订阅者。 */
export function clearInitializationPlanningRecord(application: Pick<ApplicationConfig, 'id' | 'currentVersionId'>): void {
  if (typeof window === 'undefined') return
  window.localStorage.removeItem(storageKey(application))
  window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: { applicationId: application.id, versionId: application.currentVersionId || 'current' } }))
}
