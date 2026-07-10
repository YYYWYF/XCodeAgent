import type { ReactNode } from 'react'
import { Button } from 'antd'
import { cx } from '../../utils'

type Props = {
  buttonIcon: ReactNode
  buttonLabel: string
  description: string
  icon: ReactNode
  iconVariant?: 'folder'
  loading?: boolean
  onClick: () => void
  primary?: boolean
  title: string
}

export default function WelcomeActionCard({
  buttonIcon,
  buttonLabel,
  description,
  loading,
  onClick,
  primary
}: Props): JSX.Element {
  return (
    <Button
      className={cx('welcome-action-button', primary && 'primary')}
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
