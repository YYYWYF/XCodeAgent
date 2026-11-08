import { ArrowLeftOutlined, ArrowRightOutlined, CheckCircleOutlined, ExclamationCircleOutlined, LoadingOutlined, ReloadOutlined } from '@ant-design/icons'
import { Button, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import './EndedTaskNotice.less'

type Props = {
  mode?: 'ended' | 'stopped_generation'
  unchanged?: boolean
  checkingWorkspace?: boolean
  workspaceCheckError?: string
  onRetryWorkspaceCheck?: () => void
  continuing: boolean
  disabled: boolean
  onContinue: () => Promise<void>
  onRestart?: () => void
  restartInitialPlanning?: boolean
}

/** 用紧凑状态行呈现结束结果，说明与唯一后续动作各有独立层级。 */
export default function EndedTaskNotice({
  mode = 'ended', unchanged, checkingWorkspace = false, continuing, disabled, onContinue, onRestart,
  restartInitialPlanning = false, workspaceCheckError, onRetryWorkspaceCheck
}: Props): ReactElement {
  // 普通中断沿用服务端动作；已结束正式修订可传入同一工作区基线的校验结果。
  const generationStopped = mode === 'stopped_generation'
  const checking = checkingWorkspace || (!generationStopped && unchanged === undefined)
  const canContinue = (generationStopped && unchanged === undefined) || unchanged
  const generationInvalid = generationStopped && unchanged === false
  const checkFailed = Boolean(workspaceCheckError)
  return (
    <section className={cx('ended-task-notice')} aria-label={generationStopped ? '停止生成后续操作' : '结束任务后续操作'}
      aria-live="polite" aria-busy={continuing || checking}>
      <span className={cx('ended-task-notice-icon')} aria-hidden="true">
        {checking ? <LoadingOutlined spin /> : checkFailed ? <ExclamationCircleOutlined /> : <CheckCircleOutlined />}
      </span>
      <div className={cx('ended-task-notice-copy')}>
        <Typography.Text strong>{checkFailed ? '工作区状态检查失败' : generationInvalid ? '无法继续生成' : generationStopped ? '已停止生成' : '任务已结束'}</Typography.Text>
        <Typography.Text type="secondary">
          {checking ? '正在检查工作区状态…' : checkFailed ? workspaceCheckError : generationInvalid ? restartInitialPlanning
            ? '工作区已变化，可按当前文件重新生成，完成后仍需审阅确认。'
            : '工作区已变化，无法继续原任务。请返回开发阶段重新提出需求。'
            : generationStopped && unchanged === true ? '工作区未变化，可以继续原任务的生成。'
            : generationStopped && unchanged === undefined ? '本轮修改已保留，可基于最新文件继续生成。' : checking ? '正在检查工作区状态…' : unchanged
            ? '工作区未变化，可以继续原任务。' : '工作区已变化，请重新选择开发任务。'}
        </Typography.Text>
      </div>
      {checkFailed ? (
        <Button className={cx('ended-task-notice-action')} icon={<ReloadOutlined />}
          disabled={disabled || continuing || checking || !onRetryWorkspaceCheck}
          loading={checking} onClick={onRetryWorkspaceCheck}>
          重新检查
        </Button>
      ) : !checking ? (
        <Button className={cx('ended-task-notice-action')} type={canContinue ? 'primary' : 'default'}
          icon={canContinue ? undefined : generationInvalid && !restartInitialPlanning ? <ArrowLeftOutlined /> : <ReloadOutlined />} disabled={disabled || continuing || (!canContinue && !onRestart)}
          loading={continuing} onClick={canContinue ? onContinue : onRestart}>
          {canContinue ? <>{generationStopped ? '继续生成' : '继续任务'} <ArrowRightOutlined /></> : generationInvalid
            ? restartInitialPlanning ? '重新生成' : '返回开发阶段' : '重新发起任务'}
        </Button>
      ) : null}
    </section>
  )
}
