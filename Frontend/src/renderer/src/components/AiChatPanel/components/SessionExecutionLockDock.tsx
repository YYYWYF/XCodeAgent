import { LoadingOutlined, LockOutlined } from '@ant-design/icons'
import { Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../utils'
import type { SessionRunStatus } from '../hooks/sessionRuntime'
import './SessionExecutionLockDock.less'

const { Text } = Typography

type Props = {
  phaseLabel: string
  sessionTitle?: string
  status?: SessionRunStatus | 'awaiting_user'
}

/** 在同阶段其他会话执行期间替代输入框，并说明当前历史会话仅可查看。 */
export default function SessionExecutionLockDock({
  phaseLabel,
  sessionTitle,
  status = 'running'
}: Props): ReactElement {
  const statusText =
    status === 'starting'
      ? '正在启动'
      : status === 'stopping'
        ? '正在停止'
        : status === 'awaiting_user'
          ? '等待任务确认'
          : '正在运行'
  const ownerText = sessionTitle?.trim()
    ? `“${sessionTitle.trim()}”${statusText}`
    : `其他会话${statusText}`
  return (
    <section aria-live="polite" className={cx('session-execution-lock-dock')}>
      <span aria-hidden="true" className={cx('session-execution-lock-dock-icon')}>
        {status === 'stopping' || status === 'awaiting_user' ? (
          <LockOutlined />
        ) : (
          <LoadingOutlined spin />
        )}
      </span>
      <div className={cx('session-execution-lock-dock-copy')}>
        <Text strong>
          {phaseLabel}阶段的{ownerText}
        </Text>
        <Text type="secondary">
          {status === 'awaiting_user'
            ? '当前会话只读；请在右侧阶段产物中确认或放弃任务计划。'
            : '当前会话暂时只读；该次运行结束后即可继续输入。'}
        </Text>
      </div>
    </section>
  )
}
