import type { ReactNode } from 'react'
import { cx } from '../../utils'

type Props = {
  description: string
  icon: ReactNode
  title: string
}

export default function WelcomeModalTitle({ description, icon, title }: Props): JSX.Element {
  return (
    <div className={cx('welcome-modal-title')}>
      <span className={cx('welcome-modal-title-icon')} aria-hidden="true">
        {icon}
      </span>
      <span className={cx('welcome-modal-title-copy')}>
        <strong>{title}</strong>
        <small>{description}</small>
      </span>
    </div>
  )
}
