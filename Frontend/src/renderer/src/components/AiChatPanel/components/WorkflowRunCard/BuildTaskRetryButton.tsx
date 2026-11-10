import {
  createContext,
  useContext,
  useRef,
  useState,
  type ReactElement,
  type ReactNode
} from 'react'
import { Button, Typography } from 'antd'
import type { WorkflowBuildExecutionTask } from '../../../../typings'

type RetryAction = (taskId?: string) => Promise<void>
export const BuildTaskRetryContext = createContext<{
  retry?: RetryAction
  pending: boolean
}>({ pending: false })

/** 同一轮任务共用提交锁，批量与单项重试不能在交接期间重复发送。 */
export function BuildTaskRetryProvider({
  retry,
  children
}: {
  retry?: RetryAction
  children: ReactNode
}): ReactElement {
  const lock = useRef(false)
  const [pending, setPending] = useState(false)
  /** 仅在服务端动作收口后释放锁，原任务状态仍由 AG-UI 权威进度更新。 */
  async function submit(taskId?: string): Promise<void> {
    if (lock.current || !retry) throw new Error('任务重试正在提交，请稍后再试。')
    lock.current = true
    setPending(true)
    try {
      await retry(taskId)
    } finally {
      lock.current = false
      setPending(false)
    }
  }
  return (
    <BuildTaskRetryContext.Provider value={{ retry: retry ? submit : undefined, pending }}>
      {children}
    </BuildTaskRetryContext.Provider>
  )
}

const retryableFailures = new Set([
  'invalid_structured_response',
  'runner_crash',
  'timeout',
  'tool_error',
  'model_error',
  'sandbox_error',
  'network_error'
])

/** 单项和批量按钮共用当前失败资格，不将修复或确认错误当作普通重试。 */
export function canRetryBuildTask(task: WorkflowBuildExecutionTask): boolean {
  const decision = task.failure_detail?.scheduler_decision as { action?: string } | undefined
  return (
    task.status === 'failed' &&
    Boolean(task.id || task.task_id) &&
    (decision?.action === 'retry' || retryableFailures.has(task.failure_category || ''))
  )
}

/** 在原任务卡片内提交重试并显示局部错误，不新增消息或替其他任务改变状态。 */
export default function BuildTaskRetryButton({
  task
}: {
  task: WorkflowBuildExecutionTask
}): ReactElement | null {
  const { retry, pending: submitting } = useContext(BuildTaskRetryContext)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  if (!retry || !canRetryBuildTask(task)) return null
  /** 防止连续点击重复提交，并把服务端拒绝原因留在当前卡片。 */
  const submit = async (): Promise<void> => {
    if (pending || submitting) return
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
  return (
    <span>
      <Button
        size="small"
        loading={pending}
        disabled={submitting}
        onClick={(event) => {
          event.stopPropagation()
          void submit()
        }}
      >
        重试任务
      </Button>
      {error && (
        <Typography.Text type="danger" role="alert">
          {error}
        </Typography.Text>
      )}
    </span>
  )
}
