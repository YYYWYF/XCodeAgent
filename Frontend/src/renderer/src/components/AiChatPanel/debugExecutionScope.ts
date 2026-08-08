import type { WorkflowBuildExecutionScope, WorkflowRunPayload } from '../../typings'

const BUILD_SCOPE_TYPES = new Set<WorkflowBuildExecutionScope['type']>([
  'application',
  'page',
  'data_source',
  'endpoint'
])

/** 从 Workflow 状态中的未知值读取合法构建范围。 */
function scopeFromState(value: unknown): WorkflowBuildExecutionScope | undefined {
  if (!value || typeof value !== 'object') return undefined
  const source = value as Record<string, unknown>
  const type = source.type
  if (typeof type !== 'string' || !BUILD_SCOPE_TYPES.has(type as WorkflowBuildExecutionScope['type'])) {
    return undefined
  }
  if (type === 'application') return { type }

  const targetId = typeof source.targetId === 'string' ? source.targetId.trim() : ''
  if (!targetId) return undefined
  const apiContractId =
    typeof source.apiContractId === 'string' ? source.apiContractId.trim() : ''
  return {
    type: type as WorkflowBuildExecutionScope['type'],
    targetId,
    ...(apiContractId ? { apiContractId } : {})
  }
}

/** 让调试任务默认继承当前 Workflow 的真实范围，避免页面恢复被重置成整个应用。 */
export function workflowDebugBuildScope(
  workflow?: WorkflowRunPayload
): WorkflowBuildExecutionScope {
  const stateScope = scopeFromState(workflow?.state?.buildExecutionScope)
  if (stateScope) return stateScope

  const execution = workflow?.summary.lifecycle?.activeExecutions?.[workflow.runId || '']
  if (!execution) return { type: 'application' }
  return execution.scope === 'application'
    ? { type: 'application' }
    : { type: execution.scope, targetId: execution.targetId }
}
