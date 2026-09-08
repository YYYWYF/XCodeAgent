import type { WorkflowBuildExecutionTask } from '../../../../typings'

type BuildTaskDisplayStatus = 'pending' | 'running' | 'completed' | 'failed'

/** 将已实现和经验证已满足要求统一展示为完成，保留服务端原始执行状态。 */
export function buildTaskDisplayStatus(
  status: WorkflowBuildExecutionTask['status']
): BuildTaskDisplayStatus {
  if (status === 'completed' || status === 'already_satisfied') return 'completed'
  if (status === 'running' || status === 'failed') return status
  return 'pending'
}
