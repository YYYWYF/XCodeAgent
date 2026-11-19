import type {
  ApplicationLifecycle,
  WorkflowBuildExecutionScope,
  WorkflowRunPayload
} from '../../typings'
import { workflowClarification } from './components/WorkflowRunCard/workflowClarification'
import { workflowLifecycleSnapshot } from './planExecutionMode'
import { workflowCodeChanges } from './utils'

/** 成功完成的实际代码修改提供本轮测试入口，普通问答和失败修改不冒充完成。 */
export function canEnterModifiedTesting(workflow?: WorkflowRunPayload): boolean {
  return workflow?.summary.phase === 'conversation' &&
    workflow.summary.intent === 'implementation_fix' &&
    workflow.summary.status === 'completed' &&
    Boolean(workflowCodeChanges(workflow)?.files.length)
}

/** 已结束的性能确认只能按原测试目标新建执行，旧卡片凭据只能用于展示。 */
export function performanceTestRestartTarget(
  workflow: WorkflowRunPayload,
  lifecycle?: ApplicationLifecycle
): { scope: WorkflowBuildExecutionScope; label: string; ownerSessionId: string } | undefined {
  if (
    !lifecycle ||
    Object.keys(lifecycle.activeExecutions).length > 0 ||
    lifecycle.activeFormalRevision ||
    lifecycle.extensions.testExecutionStatus === 'passed' ||
    workflow.summary.phase !== 'integration_test' ||
    workflow.summary.status !== 'requires_user_input' ||
    workflowClarification(workflow)?.mode !== 'frontend_performance_confirmation'
  ) return undefined

  const snapshot = workflowLifecycleSnapshot(workflow)
  const execution = snapshot?.activeExecutions?.[workflow.runId]
  if (
    snapshot?.application.id !== lifecycle.application.id ||
    snapshot.revision >= lifecycle.revision ||
    execution?.threadId !== workflow.threadId ||
    execution.status !== 'awaiting_user' ||
    execution.pendingInteraction?.type !== 'frontend_performance_confirmation' ||
    execution.pendingInteraction.submittedAt ||
    !execution.ownerSessionId ||
    !execution.targetId
  ) return undefined

  const scope: WorkflowBuildExecutionScope = { type: execution.scope, targetId: execution.targetId }
  if (scope.type === 'endpoint') {
    const target = execution.developmentTarget
    const publishedScope = workflow.summary.buildExecutionScope ||
      (workflow.state?.buildExecutionScope as WorkflowBuildExecutionScope | undefined)
    scope.apiContractId = target?.type === 'endpoint' && target.endpointId === execution.targetId
      ? target.apiContractId
      : publishedScope?.type === 'endpoint' && publishedScope.targetId === execution.targetId
        ? publishedScope.apiContractId
        : undefined
    if (!scope.apiContractId) return undefined
  }
  const testTarget = workflow.summary.testTarget ||
    (workflow.state?.testTarget as WorkflowRunPayload['summary']['testTarget'])
  const label = testTarget?.type === scope.type && testTarget.id === scope.targetId
    ? testTarget.label
    : scope.type === 'application' ? '当前应用' : execution.targetId
  return { scope, label, ownerSessionId: execution.ownerSessionId }
}
