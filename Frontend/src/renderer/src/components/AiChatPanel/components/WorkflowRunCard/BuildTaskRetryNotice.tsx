import { ExclamationCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import { Button, Typography } from 'antd'
import { useContext, useState } from 'react'
import type { WorkflowBuildExecutionTask } from '../../../../typings'
import { cx } from '../../../../utils'
import { BuildTaskRetryContext, canRetryBuildTask } from './BuildTaskRetryButton'
import './BuildTaskRetryNotice.less'

/** 用服务端重试次数区分同一任务的失败轮次，避免旧快照使处理栏重新闪现。 */
function failureAttempt(task: WorkflowBuildExecutionTask): string {
  return JSON.stringify([task.id || task.task_id, task.retry_count || 0])
}

/** 在原 Build 卡内展示等待状态与批量重试，失败资格及目标集合仍由服务端复验。 */
export function BuildTaskRetryNotice({
  tasks,
  waiting
}: {
  tasks: WorkflowBuildExecutionTask[]
  waiting: boolean
}): JSX.Element | null {
  const { retry, pending } = useContext(BuildTaskRetryContext)
  const [error, setError] = useState('')
  const [submittedAttempts, setSubmittedAttempts] = useState<Set<string>>(() => new Set())
  const failedTasks = tasks.filter(
    (task) => task.status === 'failed' && !submittedAttempts.has(failureAttempt(task))
  )
  const eligible = failedTasks.filter(canRetryBuildTask).length
  const failed = failedTasks.length
  if (pending || !failed || (!waiting && (!eligible || !retry))) return null
  /** 不传客户端任务列表，由服务端从当前执行范围一次性筛选并提交。 */
  async function submit(): Promise<void> {
    if (!retry || pending) return
    setError('')
    // 点击即隐藏本轮失败；入队回执早于进度帧时，也不能恢复旧任务的处理栏。
    setSubmittedAttempts(new Set(tasks.filter(canRetryBuildTask).map(failureAttempt)))
    try {
      await retry()
    } catch (reason) {
      setSubmittedAttempts(new Set())
      setError(reason instanceof Error ? reason.message : '批量重试失败，请稍后再试。')
    }
  }
  return (
    <div className={cx('workflow-build-task-retry-notice')}>
      <div className={cx('workflow-build-task-retry-copy')}>
        <ExclamationCircleOutlined aria-hidden="true" />
        <div>
          <Typography.Text strong>
            {waiting ? `等待处理 · ${failed} 个任务失败` : `${eligible} 个失败任务可重试`}
          </Typography.Text>
          <Typography.Text type="secondary">
            保留已完成任务，仅重新执行可重试的失败任务。
          </Typography.Text>
        </div>
      </div>
      {eligible > 0 && retry && (
        <Button
          type="primary"
          icon={<ReloadOutlined />}
          loading={pending}
          disabled={pending}
          onClick={() => {
            void submit()
          }}
        >
          重试全部失败任务（{eligible}）
        </Button>
      )}
      {failed > eligible && (
        <Typography.Text type="secondary">
          其余失败任务需通过对应的修复或确认入口处理。
        </Typography.Text>
      )}
      {error && (
        <Typography.Text type="danger" role="alert">
          {error}
        </Typography.Text>
      )}
    </div>
  )
}
