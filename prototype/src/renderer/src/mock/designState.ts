// 演示案例 mock 运行时状态：记录已确认详细设计的页面。
// 页面设计经 detail review 确认后标记为已设计，inspectPlanningArtifacts 据此返回最新产物状态，
// 避免"设计已完成却仍显示未设计"导致 DetailConfirmationPageSelector 锁定墙。
//
// Vite dev 下 workbench.ts（mock 剧本）是动态 import，与静态 import 的 preload.ts 会各自实例化
// 本模块；模块级 Set 会分裂成两份，导致 markPageDesigned（剧本侧）与 isPageDesigned（preload 侧）
// 读写不同状态。这里统一通过 window.__xcodeAgentMockDesignState__ 读写，保证单份运行时状态。

type MockDesignVersionState = {
  designedAgents: Set<string>
  designedPages: Set<string>
  designedEndpoints: Set<string>
  designedEntities: Set<string>
}

type MockDesignState = {
  versions: Record<string, MockDesignVersionState>
}

/** 归一化版本键，避免空值把不同迭代误合并到同一个内存状态。 */
function normalizeVersionKey(versionKey?: string): string {
  return versionKey?.trim() || 'default'
}

/** 取得当前版本的共享设计状态；剧本和 preload 通过 window 共享同一份引用。 */
function ensureVersionState(versionKey?: string): MockDesignVersionState {
  const g = window as { __xcodeAgentMockDesignState__?: MockDesignState }
  const shared = g.__xcodeAgentMockDesignState__ || { versions: {} }
  g.__xcodeAgentMockDesignState__ = shared
  const key = normalizeVersionKey(versionKey)
  const versionState = shared.versions[key] || {
    designedAgents: new Set<string>(),
    designedPages: new Set<string>(),
    designedEndpoints: new Set<string>(),
    designedEntities: new Set<string>()
  }
  shared.versions[key] = versionState
  return versionState
}

/** 读取指定版本状态；未创建过的版本只能返回未完成，不能复用其他版本的标记。 */
function readVersionState(versionKey?: string): MockDesignVersionState | undefined {
  const state = (window as { __xcodeAgentMockDesignState__?: MockDesignState })
    .__xcodeAgentMockDesignState__
  return state?.versions[normalizeVersionKey(versionKey)]
}

/** 清除指定迭代的内存设计确认，供新迭代和回滚后的新版本初始化使用。 */
export function clearDesignState(versionKey?: string): void {
  const state = (window as { __xcodeAgentMockDesignState__?: MockDesignState })
    .__xcodeAgentMockDesignState__
  if (!state) return
  delete state.versions[normalizeVersionKey(versionKey)]
}

/** 标记指定版本的页面详细设计已确认。 */
export function markPageDesigned(pageId: string, versionKey?: string): void {
  ensureVersionState(versionKey).designedPages.add(pageId)
}

/** 判断页面是否已在指定版本完成详细设计确认。 */
export function isPageDesigned(pageId: string, versionKey?: string): boolean {
  return Boolean(readVersionState(versionKey)?.designedPages.has(pageId))
}

// 接口详情审阅确认后标记为已设计，inspectPlanningArtifacts 据此返回最新契约状态。
/** 标记指定版本的接口详细设计已确认。 */
export function markEndpointDesigned(
  apiContractId: string,
  endpointId: string,
  versionKey?: string
): void {
  ensureVersionState(versionKey).designedEndpoints.add(`${apiContractId}:${endpointId}`)
}

/** 判断接口端点是否已在指定版本完成详细设计确认。 */
export function isEndpointDesigned(
  apiContractId: string,
  endpointId: string,
  versionKey?: string
): boolean {
  return Boolean(readVersionState(versionKey)?.designedEndpoints.has(`${apiContractId}:${endpointId}`))
}

/** 标记指定版本的实体详细设计已确认，供 Agent 依赖门禁重新检测。 */
export function markEntityDesigned(entityId: string, versionKey?: string): void {
  ensureVersionState(versionKey).designedEntities.add(entityId)
}

/** 判断实体是否已在指定版本完成详细设计确认。 */
export function isEntityDesigned(entityId: string, versionKey?: string): boolean {
  return Boolean(readVersionState(versionKey)?.designedEntities.has(entityId))
}

/** 标记指定版本的智能体详细设计和构建已经完成。 */
export function markAgentDesigned(agentId: string, versionKey?: string): void {
  ensureVersionState(versionKey).designedAgents.add(agentId)
}

/** 判断智能体是否已在指定 prototype 版本中完成。 */
export function isAgentDesigned(agentId: string, versionKey?: string): boolean {
  return Boolean(readVersionState(versionKey)?.designedAgents.has(agentId))
}
