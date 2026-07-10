import type { ReactNode } from 'react'
import { Button, Typography } from 'antd'
import { cx } from '../../utils'

const { Paragraph, Title } = Typography

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
  icon,
  iconVariant,
  loading,
  onClick,
  primary,
  title
}: Props) {
  return (
    <article className={cx('welcome-action-card', primary && 'primary')}>
      <div className={cx('welcome-action-icon', iconVariant)}>{icon}</div>
      <div className={cx('welcome-action-copy')}>
        <Title level={3}>{title}</Title>
        <Paragraph type="secondary">{description}</Paragraph>
      </div>
      <Button
        icon={buttonIcon}
        loading={loading}
        onClick={onClick}
        size="large"
        type={primary ? 'primary' : 'default'}
      >
        {buttonLabel}
      </Button>
    </article>
  )
}
