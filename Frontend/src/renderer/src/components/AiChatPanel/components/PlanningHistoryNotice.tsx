import { LockOutlined, SyncOutlined } from '@ant-design/icons'
import { Button, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../utils'
import type { PlanningStageAccess } from '../planningStageAccess'
import './SessionExecutionLockDock.less'

/** 用只读说明替代历史阶段输入框，沿用工作台明暗主题和已有底栏样式。 */
export default function PlanningHistoryNotice({ access, onReturn }: {
  access: PlanningStageAccess
  onReturn: () => void
}): ReactElement {
  const label = access.destination === 'development' ? '前往开发阶段'
    : access.destination === 'planning' ? '返回当前计划会话' : '返回当前需求会话'
  const title = access.transitioning ? '正在进入计划阶段' : '历史会话只读'
  return (
    <section aria-label={title} aria-busy={access.transitioning || undefined} className={cx('session-execution-lock-dock')}>
      <span aria-hidden="true" className={cx('session-execution-lock-dock-icon')}>
        {access.transitioning ? <SyncOutlined spin /> : <LockOutlined />}
      </span>
      <div className={cx('session-execution-lock-dock-copy')}>
        <Typography.Text strong>{title}</Typography.Text>
        <Typography.Text type="secondary">{access.message}</Typography.Text>
      </div>
      {access.destination ? <Button className={cx('session-execution-lock-dock-action')} onClick={onReturn}>
        {label}
      </Button> : null}
    </section>
  )
}
