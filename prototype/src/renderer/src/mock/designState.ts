// 演示案例 mock 运行时状态：记录已确认详细设计的页面。
// 页面设计经 detail review 确认后标记为已设计，inspectPlanningArtifacts 据此返回最新产物状态，
// 避免"设计已完成却仍显示未设计"导致 DetailConfirmationPageSelector 锁定墙。
//
// Vite dev 下 workbench.ts（mock 剧本）是动态 import，与静态 import 的 preload.ts 会各自实例化
// 本模块；模块级 Set 会分裂成两份，导致 markPageDesigned（剧本侧）与 isPageDesigned（preload 侧）
// 读写不同状态。这里统一通过 window.__xcodeAgentMockDesignState__ 读写，保证单份运行时状态。

type MockDesignState = {
  designedAgents: Set<string>
  designedPages: Set<string>
  designedEndpoints: Set<string>
}

function ensureState(): MockDesignState {
  const g = window as { __xcodeAgentMockDesignState__?: MockDesignState }
  const shared = g.__xcodeAgentMockDesignState__ || {
    designedAgents: new Set<string>(),
    designedPages: new Set<string>(),
    designedEndpoints: new Set<string>()
  }
  shared.designedAgents ||= new Set<string>()
  g.__xcodeAgentMockDesignState__ = shared
  return shared
}

function readState(): MockDesignState | undefined {
  return (window as { __xcodeAgentMockDesignState__?: MockDesignState })
    .__xcodeAgentMockDesignState__
}

export function markPageDesigned(pageId: string): void {
  ensureState().designedPages.add(pageId)
}

export function isPageDesigned(pageId: string): boolean {
  return Boolean(readState()?.designedPages.has(pageId))
}

// 接口详情审阅确认后标记为已设计，inspectPlanningArtifacts 据此返回最新契约状态。
export function markEndpointDesigned(apiContractId: string, endpointId: string): void {
  ensureState().designedEndpoints.add(`${apiContractId}:${endpointId}`)
}

export function isEndpointDesigned(apiContractId: string, endpointId: string): boolean {
  return Boolean(readState()?.designedEndpoints.has(`${apiContractId}:${endpointId}`))
}

/** 标记智能体详细设计和构建已经完成。 */
export function markAgentDesigned(agentId: string): void {
  ensureState().designedAgents.add(agentId)
}

/** 判断智能体是否已在本轮 prototype 运行中完成。 */
export function isAgentDesigned(agentId: string): boolean {
  return Boolean(readState()?.designedAgents.has(agentId))
}
