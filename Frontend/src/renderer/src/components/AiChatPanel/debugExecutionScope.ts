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

  const targetId =
    typeof (source.targetId || source.target_id) === 'string'
      ? String(source.targetId || source.target_id).trim()
      : ''
  if (!targetId) return undefined
  const apiContractId =
    typeof (source.apiContractId || source.api_contract_id) === 'string'
      ? String(source.apiContractId || source.api_contract_id).trim()
      : ''
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
  const stateScope = scopeFromState(
    workflow?.state?.buildExecutionScope || workflow?.state?.build_execution_scope
  )
  if (stateScope) return stateScope

  const resultScope = scopeFromState(
    workflow?.result?.buildExecutionScope || workflow?.result?.build_execution_scope
  )
  if (resultScope) return resultScope

  const execution = workflow?.summary.lifecycle?.activeExecutions?.[workflow.runId || '']
  if (!execution) return { type: 'application' }
  const stateApiContractId =
    workflow?.state?.selectedApiContractId || workflow?.state?.selected_api_contract_id
  const resultApiContractId =
    workflow?.result?.selectedApiContractId || workflow?.result?.selected_api_contract_id
  const apiContractId = String(stateApiContractId || resultApiContractId || '').trim()
  return execution.scope === 'application'
    ? { type: 'application' }
    : {
        type: execution.scope,
        targetId: execution.targetId,
        ...(execution.scope === 'endpoint' && apiContractId ? { apiContractId } : {})
      }
}
