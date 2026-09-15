import { useEffect, useMemo, useState } from 'react'
import { createBusinessObjects, withConfirmedBindings, type BusinessObject } from './model'
import { readDataSources } from '../DataSources/catalog'
import { presetCompletedVersionIds } from '../../mock/fixtures'

const CHANGE_EVENT = 'aistudio:prototype:business-objects-changed'

// 缓存键前缀集中定义：结构升级即换版本号弃旧缓存，回退/迭代的清理也引用同一前缀，
// 避免键升级后清理过滤器仍停在旧版本号、把复位变成空操作。
const STORAGE_PREFIX = 'aistudio:prototype:business-objects:v10'

/**
 * 根据应用名、实体集合和版本生成稳定的演示缓存键。
 * 版本必须参与键名：实体绑定是版本内的交付事实，发起新迭代后实体要回到未开始状态，
 * 已生成版本的只读回看则继续呈现该版本自己的绑定结果。
 * v10：v1.3 基线定为"验收完成待生成版本"，实体按完成态呈现；换键弃掉历史实验缓存
 * （含中间方案写入的未绑定缓存），保证打开即重落确认绑定基线。
 */
function storageKey(spec: Record<string, unknown>, versionId = 'current'): string {
  const app = (spec.app_info || {}) as Record<string, unknown>
  const entityIds = (Array.isArray(spec.entities) ? spec.entities : [])
    .map((item) => String((item as Record<string, unknown>).id || ''))
    .join('-')
  return `${STORAGE_PREFIX}:${String(app.name || 'application')}:${entityIds}:${versionId}`
}

/**
 * 缓存未命中时的初始化：预置完成版本（lifecycle 测试与验收全通过，见
 * mock-data 的 presetCompletedVersionIds）没有运行历史，版本隔离缓存键让它读不到
 * 任何绑定事实，按空缓存推导会得到「未开始」，与 lifecycle 全 passed 的完成态矛盾。
 * 这里按「确认绑定」的同一权威路径（withConfirmedBindings）落实全部操作并写回缓存，
 * 让产物目录、顶部进度与真实走完旅程的版本同构；其余版本从需求意向状态开始。
 */
function initializeObjects(spec: Record<string, unknown>, versionId: string): BusinessObject[] {
  const objects = createBusinessObjects(spec)
  if (!presetCompletedVersionIds.has(versionId)) return objects
  try {
    const completed = objects.map((object) => withConfirmedBindings(object, readDataSources()))
    window.localStorage.setItem(storageKey(spec, versionId), JSON.stringify(completed))
    return completed
  } catch {
    // 持久化失败只影响下次刷新，本次演示仍按完成态呈现。
    return objects.map((object) => withConfirmedBindings(object, readDataSources()))
  }
}

/** 读取当前应用的实体计划；尚未配置时从需求说明书生成一次。 */
function readObjects(spec: Record<string, unknown>, versionId: string): BusinessObject[] {
  if (typeof window === 'undefined') return createBusinessObjects(spec)
  try {
    const saved = window.localStorage.getItem(storageKey(spec, versionId))
    if (!saved) return initializeObjects(spec, versionId)
    const parsed = JSON.parse(saved) as BusinessObject[]
    // 实现结构升级到 v3 后旧缓存缺失字段时回落重建，保证演示状态完整。
    const valid =
      Array.isArray(parsed) &&
      parsed.every(
        (object) =>
          Array.isArray(object.operations) &&
          object.operations.every(
            (operation) =>
              operation.implementation &&
              Array.isArray(operation.implementation.bindings) &&
              Array.isArray(operation.implementation.mappings)
          )
      )
    return valid ? parsed : initializeObjects(spec, versionId)
  } catch {
    return initializeObjects(spec, versionId)
  }
}

/** 供开发工作流剧本等非组件场景读取实体快照，与 hook 共用同一份缓存键。 */
export function readBusinessObjectsSnapshot(
  spec: Record<string, unknown>,
  versionId = 'current'
): BusinessObject[] {
  return readObjects(spec, versionId)
}

/** 供开发工作流剧本写回绑定确认结果：保存并广播变更，让已挂载的阶段面板同步刷新。 */
export function saveBusinessObjects(
  spec: Record<string, unknown>,
  next: BusinessObject[],
  versionId = 'current'
): void {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(storageKey(spec, versionId), JSON.stringify(next))
  window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: storageKey(spec, versionId) }))
}

/**
 * 发起新迭代/回退时清除该版本名下的实体绑定缓存。
 * 工作迭代不持久化，重载后版本 id 会被复用，残留的绑定事实会让新迭代
 * 一进开发阶段就显示已完成；按完整版本键精确清除，不影响其它版本的回看。
 */
export function resetBusinessObjectsCache(versionId: string): void {
  if (typeof window === 'undefined' || !versionId) return
  const suffix = `:${versionId}`
  const staleKeys = Object.keys(window.localStorage).filter(
    (key) => key.startsWith(`${STORAGE_PREFIX}:`) && key.endsWith(suffix)
  )
  staleKeys.forEach((key) => window.localStorage.removeItem(key))
}

/** 让计划与开发阶段共享同一份实体演示状态；版本是缓存键的一部分。 */
export function useBusinessObjects(
  spec: Record<string, unknown>,
  versionId = 'current'
): [BusinessObject[], (next: BusinessObject[]) => void] {
  const key = useMemo(() => storageKey(spec, versionId), [spec, versionId])
  const [objects, setObjects] = useState<BusinessObject[]>(() => readObjects(spec, versionId))
  useEffect(() => {
    setObjects(readObjects(spec, versionId))
    const refresh = (event: Event): void => {
      const changedKey = (event as CustomEvent<string>).detail
      if (!changedKey || changedKey === key) setObjects(readObjects(spec, versionId))
    }
    window.addEventListener(CHANGE_EVENT, refresh)
    return () => window.removeEventListener(CHANGE_EVENT, refresh)
  }, [key, spec, versionId])
  /** 保存实体计划，并同步通知其它已挂载的阶段面板。 */
  const save = (next: BusinessObject[]): void => {
    setObjects(next)
    window.localStorage.setItem(key, JSON.stringify(next))
    window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: key }))
  }
  return [objects, save]
}
