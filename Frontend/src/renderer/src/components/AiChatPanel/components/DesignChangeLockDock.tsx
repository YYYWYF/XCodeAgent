import { LockOutlined, LoadingOutlined, PauseCircleOutlined } from '@ant-design/icons'
import { Alert, Button, Typography } from 'antd'
import { useState } from 'react'
import type { ReactElement } from 'react'
import { cx } from '../../../utils'
import './DesignChangeLockDock.less'

const { Text } = Typography

type Props = {
  disabled?: boolean
  onStart: () => Promise<void>
}

/** 在设计主 Workflow 运行期间锁定底部自由输入，并提供进入意图识别模式的唯一入口。 */
export default function DesignChangeLockDock({ disabled = false, onStart }: Props): ReactElement {
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState('')

  /** 先安全停止设计主 Workflow，成功后才开放自由变更输入框。 */
  const handleStart = async (): Promise<void> => {
    if (disabled || starting) return
    setStarting(true)
    setError('')
    try {
      await onStart()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '暂停当前设计流程失败，请重试。')
    } finally {
      setStarting(false)
    }
  }

  return (
    <section
      aria-busy={starting}
      aria-live="polite"
      className={cx('design-change-lock-dock', starting && 'starting')}
    >
      <div className={cx('design-change-lock-dock-main')}>
        <span className={cx('design-change-lock-dock-icon')} aria-hidden="true">
          {starting ? <LoadingOutlined spin /> : <LockOutlined />}
        </span>
        <div className={cx('design-change-lock-dock-copy')}>
          <Text strong>规划流程进行中，底部自由输入已锁定</Text>
          <Text type="secondary">
            上方卡片只处理当前确认；自由输入会先识别变更意图，不受当前阶段限制。
          </Text>
        </div>
        <Button
          disabled={disabled || starting}
          icon={<PauseCircleOutlined />}
          loading={starting}
          onClick={() => void handleStart()}
          type="primary"
        >
          {starting ? '正在暂停' : '暂停并自由输入'}
        </Button>
      </div>
      {error ? <Alert closable message={error} onClose={() => setError('')} type="error" /> : null}
    </section>
  )
}
