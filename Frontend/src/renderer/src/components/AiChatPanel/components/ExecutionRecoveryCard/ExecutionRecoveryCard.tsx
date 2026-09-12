import { LoadingOutlined, WarningOutlined } from '@ant-design/icons'
import { Button, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import type { ExecutionRecoveryCandidate } from '../../../../typings'
import './ExecutionRecoveryCard.less'

const { Text } = Typography

export type ExecutionRecoveryCardProps = {
  recovery: ExecutionRecoveryCandidate
  loading: boolean
  disabled?: boolean
  error?: string
  onContinue: () => void
}

/** 在当前会话消息与输入框之间展示 Backend 明确允许的中断执行恢复动作。 */
export default function ExecutionRecoveryCard({
  recovery,
  loading,
  disabled = false,
  error,
  onContinue
}: ExecutionRecoveryCardProps): ReactElement {
  const ready = recovery.availability === 'ready' && recovery.canContinue
  const blocked = recovery.availability === 'blocked'
  const description = ready
    ? '执行过程中发生中断，已保存可恢复现场。'
    : blocked
      ? '工作区或流程状态已经发生变化，无法直接从旧现场继续。'
      : '当前步骤暂不能自动继续。'
  return (
    <section aria-live="polite" className={cx('execution-recovery-card')}>
      <span aria-hidden="true" className={cx('execution-recovery-card-icon')}>
        {loading ? <LoadingOutlined spin /> : <WarningOutlined />}
      </span>
      <div className={cx('execution-recovery-card-copy')}>
        <Text strong>{ready ? '上一次执行未正常完成' : '上一次执行已中断'}</Text>
        <Text type="secondary">{description}</Text>
        {error ? (
          <Text className={cx('execution-recovery-card-error')}>继续执行失败：{error}</Text>
        ) : null}
      </div>
      {ready ? (
        <Button
          className={cx('execution-recovery-card-action')}
          disabled={disabled || !recovery.canContinue}
          icon={loading ? <LoadingOutlined spin /> : undefined}
          loading={loading}
          onClick={onContinue}
          type="primary"
        >
          继续执行
        </Button>
      ) : null}
    </section>
  )
}
