import { createContext, useContext, useState, type ReactElement } from 'react'
import { Button, Typography } from 'antd'
import type { WorkflowBuildExecutionTask } from '../../../../typings'

export const BuildTaskRetryContext = createContext<((taskId: string) => Promise<void>) | undefined>(undefined)

const retryableFailures = new Set([
  'invalid_structured_response', 'runner_crash', 'timeout', 'tool_error',
  'model_error', 'sandbox_error', 'network_error'
])

/** 在原任务卡片内提交重试并显示局部错误，不新增消息或替其他任务改变状态。 */
export default function BuildTaskRetryButton({ task }: { task: WorkflowBuildExecutionTask }): ReactElement | null {
  const retry = useContext(BuildTaskRetryContext)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const decision = task.failure_detail?.scheduler_decision as { action?: string } | undefined
  if (!retry || task.status !== 'failed' || !(task.id || task.task_id) ||
      !(decision?.action === 'retry' || retryableFailures.has(task.failure_category || ''))) return null
  /** 防止连续点击重复提交，并把服务端拒绝原因留在当前卡片。 */
  const submit = async (): Promise<void> => {
    if (pending) return
    setPending(true)
    setError('')
    try {
      await retry(String(task.id || task.task_id))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '任务重试失败。')
    } finally {
      setPending(false)
    }
  }
  return <span>
    <Button size="small" loading={pending} onClick={(event) => { event.stopPropagation(); void submit() }}>
      重试任务
    </Button>
    {error && <Typography.Text type="danger" role="alert">{error}</Typography.Text>}
  </span>
}
