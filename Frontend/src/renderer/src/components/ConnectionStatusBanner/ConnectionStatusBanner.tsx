import { LoadingOutlined, SyncOutlined, WifiOutlined } from '@ant-design/icons'
import { Button, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { ConnectionState } from '../../service/connectionState'
import { cx } from '../../utils'
import './ConnectionStatusBanner.less'

const { Text } = Typography

export type ConnectionStatusBannerProps = {
  connection: ConnectionState
  onReconnect?: () => void
  scope?: 'backend' | 'preview'
}

/** 独立展示 renderer 到 Backend 的连接状态，不生成或解释 Workflow Recovery。 */
export default function ConnectionStatusBanner({
  connection,
  onReconnect,
  scope = 'backend'
}: ConnectionStatusBannerProps): ReactElement | null {
  if (connection.status === 'healthy') return null
  const pending = connection.status === 'connecting' || connection.status === 'reconnecting'
  const subject = scope === 'preview' ? '预览状态服务' : 'Backend'
  const title =
    connection.status === 'connecting'
      ? `正在连接 ${subject}`
      : connection.status === 'reconnecting'
        ? `正在重新连接 ${subject}`
        : scope === 'preview' ? '预览状态连接中断' : 'Backend 连接中断'
  const message = pending
    ? '正在同步最新状态和可用的恢复入口。'
    : connection.lastError || '连接已中断，暂时无法同步最新状态。'

  return (
    <section
      aria-label={title}
      aria-live="polite"
      className={cx('connection-status-banner')}
      data-testid="connection-status-banner"
      role="status"
    >
      <span aria-hidden="true" className={cx('connection-status-banner-icon')}>
        {pending ? <LoadingOutlined spin /> : <WifiOutlined />}
      </span>
      <div className={cx('connection-status-banner-copy')}>
        <Text className={cx('connection-status-banner-title')} strong>
          {title}
        </Text>
        <Text className={cx('connection-status-banner-message')}>{message}</Text>
      </div>
      {!pending && onReconnect ? (
        <Button
          className={cx('connection-status-banner-action')}
          icon={<SyncOutlined />}
          onClick={onReconnect}
          type="default"
        >
          重新同步
        </Button>
      ) : null}
    </section>
  )
}
