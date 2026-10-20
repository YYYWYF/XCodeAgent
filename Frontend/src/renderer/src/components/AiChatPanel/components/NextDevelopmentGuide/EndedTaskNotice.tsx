import { ArrowRightOutlined, CheckCircleOutlined, LoadingOutlined, ReloadOutlined } from '@ant-design/icons'
import { Button, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import './EndedTaskNotice.less'

type Props = {
  mode?: 'ended' | 'stopped_generation'
  unchanged?: boolean
  continuing: boolean
  disabled: boolean
  onContinue: () => Promise<void>
  onRestart?: () => void
}

/** 用紧凑状态行呈现结束结果，说明与唯一后续动作各有独立层级。 */
export default function EndedTaskNotice({
  mode = 'ended', unchanged, continuing, disabled, onContinue, onRestart
}: Props): ReactElement {
  // 规划停止已由服务端签发继续动作，无需开发任务的工作区变化资格检查。
  const generationStopped = mode === 'stopped_generation'
  const checking = !generationStopped && unchanged === undefined
  const canContinue = generationStopped || unchanged
  return (
    <section className={cx('ended-task-notice')} aria-label={generationStopped ? '停止生成后续操作' : '结束任务后续操作'}
      aria-live="polite" aria-busy={continuing || checking}>
      <span className={cx('ended-task-notice-icon')} aria-hidden="true">
        {checking ? <LoadingOutlined spin /> : <CheckCircleOutlined />}
      </span>
      <div className={cx('ended-task-notice-copy')}>
        <Typography.Text strong>{generationStopped ? '已停止生成' : '任务已结束'}</Typography.Text>
        <Typography.Text type="secondary">
          {generationStopped ? '本轮修改已保留，可基于最新文件继续生成。' : checking ? '正在检查工作区状态…' : unchanged
            ? '工作区未变化，可以继续原任务。' : '工作区已变化，请重新选择开发任务。'}
        </Typography.Text>
      </div>
      {!checking ? (
        <Button className={cx('ended-task-notice-action')} type={canContinue ? 'primary' : 'default'}
          icon={canContinue ? undefined : <ReloadOutlined />} disabled={disabled || continuing || (!canContinue && !onRestart)}
          loading={continuing} onClick={canContinue ? onContinue : onRestart}>
          {canContinue ? <>{generationStopped ? '继续生成' : '继续任务'} <ArrowRightOutlined /></> : '重新发起任务'}
        </Button>
      ) : null}
    </section>
  )
}
