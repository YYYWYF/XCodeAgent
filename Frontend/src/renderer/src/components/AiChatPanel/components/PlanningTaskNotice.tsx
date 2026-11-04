import { LockOutlined } from '@ant-design/icons'
import { Button, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../utils'
import type { PlanningTaskBlock } from '../planningTaskAdmission'
import './SessionExecutionLockDock.less'

/** 复用明暗主题底栏，说明开发被当前需求或计划阻挡并返回真实执行会话。 */
export default function PlanningTaskNotice({ block, onReturn }: {
  block: PlanningTaskBlock
  onReturn: () => void
}): ReactElement {
  return (
    <section aria-label="请先处理当前规划" role="status" className={cx('session-execution-lock-dock')}>
      <span aria-hidden="true" className={cx('session-execution-lock-dock-icon')}><LockOutlined /></span>
      <div className={cx('session-execution-lock-dock-copy')}>
        <Typography.Text strong>{block.message}</Typography.Text>
      </div>
      <Button className={cx('session-execution-lock-dock-action')} onClick={onReturn}>
        {block.actionLabel}
      </Button>
    </section>
  )
}
