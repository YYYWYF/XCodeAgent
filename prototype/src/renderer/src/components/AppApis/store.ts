import { useEffect, useMemo, useState } from 'react'
import { createAppApis, withConfirmedBindings, withLiveSourceNames, type AppApi } from './model'
import { readDataSources } from '../DataSources/catalog'
import { presetCompletedVersionIds } from '../../mock/fixtures'

const CHANGE_EVENT = 'devagentstudio:prototype:app-apis-changed'

// 缓存键前缀集中定义：结构升级即换版本号弃旧缓存，回退/迭代的清理也引用同一前缀。
// v2：模型扁平化（接口即产物，AppApi 不再有 operations 分组），换键弃掉旧结构缓存。
// v3：契约补入参 request，绑定按来源分流为增删查改模板 / 外部参数适配，换键弃掉旧结构缓存。
// v4：外部绑定新增入参对齐 requestParamMap，换键弃掉旧结构缓存。
// v5：入参对齐反转为显式连接 requestFeeders（外部入参 → 契约入参，空串=固定值），换键弃掉旧缓存。
const STORAGE_PREFIX = 'devagentstudio:prototype:app-apis:v5'

/**
 * 根据应用名、应用API集合和版本生成稳定的演示缓存键。
 * 版本必须参与键名：应用API绑定是版本内的交付事实，发起新迭代后应用API要回到未开始状态，
 * 已生成版本的只读回看则继续呈现该版本自己的绑定结果。
 */
function storageKey(spec: Record<string, unknown>, versionId = 'current'): string {
  const app = (spec.app_info || {}) as Record<string, unknown>
  const apiIds = (Array.isArray(spec.apis) ? spec.apis : [])
    .map((item) => String((item as Record<string, unknown>).id || ''))
    .join('-')
  return `${STORAGE_PREFIX}:${String(app.name || 'application')}:${apiIds}:${versionId}`
}

/**
 * 缓存未命中时的初始化：预置完成版本（lifecycle 测试与验收全通过，见
 * mock-data 的 presetCompletedVersionIds）没有运行历史，版本隔离缓存键让它读不到
 * 任何绑定事实，按空缓存推导会得到「未开始」，与 lifecycle 全 passed 的完成态矛盾。
 * 这里按「确认绑定」的同一权威路径（withConfirmedBindings）落实全部接口并写回缓存，
 * 让产物目录、顶部进度与真实走完旅程的版本同构；其余版本从需求意向状态开始。
 */
function initializeObjects(
  spec: Record<string, unknown>,
  versionId: string,
  technicalPlan?: Record<string, unknown>
): AppApi[] {
  const objects = createAppApis(spec, technicalPlan)
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

/** 读取当前应用的应用API计划；尚未配置时从需求说明书生成一次。 */
function readObjects(
  spec: Record<string, unknown>,
  versionId: string,
  technicalPlan?: Record<string, unknown>
): AppApi[] {
  if (typeof window === 'undefined') return createAppApis(spec, technicalPlan)
  try {
    const saved = window.localStorage.getItem(storageKey(spec, versionId))
    if (!saved) return initializeObjects(spec, versionId, technicalPlan)
    const parsed = JSON.parse(saved) as AppApi[]
    // 结构升级后旧缓存缺失字段时回落重建，保证演示状态完整。
    const valid =
      Array.isArray(parsed) &&
      parsed.every(
        (object) =>
          typeof object.method === 'string' &&
          typeof object.path === 'string' &&
          Array.isArray(object.request) &&
          Array.isArray(object.response) &&
          object.implementation &&
          Array.isArray(object.implementation.bindings) &&
          Array.isArray(object.implementation.mappings) &&
          Array.isArray(object.implementation.conditions) &&
          Array.isArray(object.implementation.setters) &&
          Boolean(object.implementation.expressions) &&
          Boolean(object.implementation.requestFeeders)
      )
    if (!valid) return initializeObjects(spec, versionId, technicalPlan)
    const sources = readDataSources()
    // 读取边界统一契约形态：存量缓存里的纯中文名出参/无 code 入参归一为 code+name 结构；
    // 同时让已确认绑定里的来源名跟随当前目录（目录改名后视图不再展示旧快照名）。
    return parsed.map((object) =>
      withLiveSourceNames(
        {
          ...object,
          request: object.request.map((param) => ({ ...param, code: param.code || '' })),
          response: object.response.map((item) =>
            typeof item === 'string' ? { code: '', name: item } : item
          )
        },
        sources
      )
    )
  } catch {
    return initializeObjects(spec, versionId, technicalPlan)
  }
}

/** 供开发工作流剧本等非组件场景读取应用API快照，与 hook 共用同一份缓存键。 */
export function readAppApisSnapshot(
  spec: Record<string, unknown>,
  versionId = 'current',
  technicalPlan?: Record<string, unknown>
): AppApi[] {
  return readObjects(spec, versionId, technicalPlan)
}

/** 供开发工作流剧本写回绑定确认结果：保存并广播变更，让已挂载的阶段面板同步刷新。 */
export function saveAppApis(
  spec: Record<string, unknown>,
  next: AppApi[],
  versionId = 'current'
): void {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(storageKey(spec, versionId), JSON.stringify(next))
  window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: storageKey(spec, versionId) }))
}

/**
 * 发起新迭代/回退时清除该版本名下的应用API绑定缓存。
 * 工作迭代不持久化，重载后版本 id 会被复用，残留的绑定事实会让新迭代
 * 一进开发阶段就显示已完成；按完整版本键精确清除，不影响其它版本的回看。
 */
export function resetAppApisCache(versionId: string): void {
  if (typeof window === 'undefined' || !versionId) return
  const suffix = `:${versionId}`
  const staleKeys = Object.keys(window.localStorage).filter(
    (key) => key.startsWith(`${STORAGE_PREFIX}:`) && key.endsWith(suffix)
  )
  staleKeys.forEach((key) => window.localStorage.removeItem(key))
}

/** 让计划与开发阶段共享同一份应用API演示状态；版本是缓存键的一部分。 */
export function useAppApis(
  spec: Record<string, unknown>,
  versionId = 'current',
  technicalPlan?: Record<string, unknown>
): [AppApi[], (next: AppApi[]) => void] {
  const key = useMemo(() => storageKey(spec, versionId), [spec, versionId])
  const [objects, setObjects] = useState<AppApi[]>(() =>
    readObjects(spec, versionId, technicalPlan)
  )
  useEffect(() => {
    setObjects(readObjects(spec, versionId, technicalPlan))
    const refresh = (event: Event): void => {
      const changedKey = (event as CustomEvent<string>).detail
      if (!changedKey || changedKey === key) setObjects(readObjects(spec, versionId, technicalPlan))
    }
    window.addEventListener(CHANGE_EVENT, refresh)
    return () => window.removeEventListener(CHANGE_EVENT, refresh)
  }, [key, spec, versionId, technicalPlan])
  /** 保存应用API计划，并同步通知其它已挂载的阶段面板。 */
  const save = (next: AppApi[]): void => {
    setObjects(next)
    window.localStorage.setItem(key, JSON.stringify(next))
    window.dispatchEvent(new CustomEvent(CHANGE_EVENT, { detail: key }))
  }
  return [objects, save]
}
