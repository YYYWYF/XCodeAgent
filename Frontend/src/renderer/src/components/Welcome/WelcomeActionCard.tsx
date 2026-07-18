import type { ReactNode } from 'react'
import { Button } from 'antd'
import { cx } from '../../utils'

type Props = {
  buttonIcon: ReactNode
  buttonLabel: string
  description: string
  disabled?: boolean
  icon: ReactNode
  iconVariant?: 'folder'
  loading?: boolean
  onClick: () => void
  primary?: boolean
  title: string
}

// 渲染欢迎页统一尺寸与视觉规范的操作按钮。
export default function WelcomeActionCard({
  buttonIcon,
  buttonLabel,
  description,
  disabled,
  loading,
  onClick,
  primary
}: Props): JSX.Element {
  return (
    <Button
      className={cx('welcome-action-button', primary && 'primary')}
      disabled={disabled}
      icon={buttonIcon}
      loading={loading}
      onClick={onClick}
      size="large"
      title={description}
      type={primary ? 'primary' : 'default'}
    >
      {buttonLabel}
    </Button>
  )
}
