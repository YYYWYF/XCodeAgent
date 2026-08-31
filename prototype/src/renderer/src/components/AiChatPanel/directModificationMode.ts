import type { WorkflowDebugOptions, WorkflowRunPayload } from '../../typings'

/** 判断 Workflow 是否来自独立简单修改 Graph，兼容分类完成前后不同投影阶段。 */
export function isDirectModificationWorkflow(workflow: WorkflowRunPayload | undefined): boolean {
  if (workflow?.summary?.phase === 'direct_modification') return true
  return ['frontend', 'backend', 'fullstack', 'unknown'].includes(String(workflow?.summary?.owner))
}

/** 判断简单模式是否正等待用户通过自由输入框补充修改需求。 */
export function isDirectModificationWaitingForInput(
  workflow: WorkflowRunPayload | undefined
): boolean {
  return (
    isDirectModificationWorkflow(workflow) && workflow?.summary.status === 'requires_user_input'
  )
}

/** 选择普通消息使用的执行端点，并保证简单模式澄清始终沿同一 Graph 接续。 */
export function shouldUseDirectModification(
  enabled: boolean,
  workflow: WorkflowRunPayload | undefined,
  workflowDebug: WorkflowDebugOptions | undefined
): boolean {
  if (workflowDebug?.enabled) return false
  if (isDirectModificationWorkflow(workflow)) return true
  if (!enabled) return false
  const status = String(workflow?.summary?.status || '')
  return !['running', 'in_progress', 'requires_user_input', 'paused', 'stopping'].includes(status)
}
