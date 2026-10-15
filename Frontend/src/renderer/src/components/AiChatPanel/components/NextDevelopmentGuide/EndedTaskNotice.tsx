import { ArrowRightOutlined, CheckCircleOutlined, LoadingOutlined, ReloadOutlined } from '@ant-design/icons'
import { Button, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import './EndedTaskNotice.less'

type Props = {
  unchanged?: boolean
  continuing: boolean
  disabled: boolean
  onContinue: () => Promise<void>
  onRestart: () => void
}

/** 用紧凑状态行呈现结束结果，说明与唯一后续动作各有独立层级。 */
export default function EndedTaskNotice({
  unchanged, continuing, disabled, onContinue, onRestart
}: Props): ReactElement {
  const checking = unchanged === undefined
  return (
    <section className={cx('ended-task-notice')} aria-label="结束任务后续操作"
      aria-live="polite" aria-busy={continuing || checking}>
      <span className={cx('ended-task-notice-icon')} aria-hidden="true">
        {checking ? <LoadingOutlined spin /> : <CheckCircleOutlined />}
      </span>
      <div className={cx('ended-task-notice-copy')}>
        <Typography.Text strong>任务已结束</Typography.Text>
        <Typography.Text type="secondary">
          {checking ? '正在检查工作区状态…' : unchanged
            ? '工作区未变化，可以继续原任务。' : '工作区已变化，请重新选择开发任务。'}
        </Typography.Text>
      </div>
      {!checking ? (
        <Button className={cx('ended-task-notice-action')} type={unchanged ? 'primary' : 'default'}
          icon={unchanged ? undefined : <ReloadOutlined />} disabled={disabled || continuing}
          loading={continuing} onClick={unchanged ? onContinue : onRestart}>
          {unchanged ? <>继续任务 <ArrowRightOutlined /></> : '重新发起任务'}
        </Button>
      ) : null}
    </section>
  )
}
