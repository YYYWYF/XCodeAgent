export type AgentSurfaceProductPlan = Record<string, unknown>

export type AgentSurfaceWorkflowIdentity = {
  runId: string
  threadId: string
}

export type AgentSurfaceProductPlanSelection = AgentSurfaceWorkflowIdentity & {
  productPlan: AgentSurfaceProductPlan
  sourceProductPlanKey: string
}

/** 提取不会因单个 Surface 启停而变化的 ProductPlan 产物身份。 */
export function agentSurfaceProductPlanKey(
  productPlan: AgentSurfaceProductPlan | undefined
): string {
  if (!productPlan) return ''
  return [
    productPlan.schema_version,
    productPlan.version,
    productPlan.generated_at,
    productPlan.requirement_spec_sha256
  ]
    .map((value) => String(value || ''))
    .join(':')
}

/** 当前规划轮次优先使用刚保存的浮窗选择，下一轮自动回到新的 Workflow 快照。 */
export function selectedAgentSurfaceProductPlan(
  selection: AgentSurfaceProductPlanSelection | undefined,
  identity: AgentSurfaceWorkflowIdentity,
  workflowProductPlan: AgentSurfaceProductPlan | undefined
): AgentSurfaceProductPlan | undefined {
  if (
    selection &&
    workflowProductPlan?.confirmation_status === 'pending_user_confirmation' &&
    selection.runId === identity.runId &&
    selection.threadId === identity.threadId &&
    selection.sourceProductPlanKey === agentSurfaceProductPlanKey(workflowProductPlan)
  ) {
    return selection.productPlan
  }
  return workflowProductPlan
}
