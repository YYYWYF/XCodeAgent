import type { WorkflowBuildExecutionTask } from '../../../../typings'

const modelFailureCategories = new Set([
  'model_error',
  'network_error',
  'timeout',
  'invalid_structured_response'
])

/** 模型调用失败可能被包装为执行器异常，需同时检查分类和原始失败诊断。 */
function isModelCallFailure(task: WorkflowBuildExecutionTask): boolean {
  if (modelFailureCategories.has(task.failure_category || '')) return true
  const diagnostic = `${task.failure_reason || ''}\n${JSON.stringify(task.failure_detail || {})}`
  return /ModelTransmitError|APIConnectionError|APITimeoutError|RateLimitError|模型(?:转发|调用|请求|连接|响应|服务)[^\n。；]{0,40}(?:失败|异常|超时|中断|不可用)/i.test(diagnostic)
}

/** 前端只为任务计划问题提供修复入口，模型或传输失败交给原重试操作。 */
export function canRepairBuildTask(task: WorkflowBuildExecutionTask): boolean {
  return Boolean(task.id || task.task_id) &&
    (task.status === 'failed' || Boolean(task.taskRepairEligible)) &&
    ['backend', 'frontend'].includes(task.owner || '') && task.kind !== 'repair' &&
    (!task.execution_strategy || task.execution_strategy === 'agent') &&
    !isModelCallFailure(task)
}
