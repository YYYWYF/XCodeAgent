import { CreateApplicationAction, OpenWorkspaceAction, WelcomeHero } from '../components/Welcome'
import type { ApplicationConfig } from '../typings'
import { cx } from '../utils'
import './WelcomePage.less'

type Props = {
  onOpenApplication: (application: ApplicationConfig) => void
}

export default function WelcomePage({ onOpenApplication }: Props) {
  return (
    <main className={cx('welcome-page')}>
      <section className={cx('welcome-shell')}>
        <WelcomeHero />

        <section className={cx('welcome-actions')} aria-label="开始使用 XCodeAgent">
          <CreateApplicationAction onOpenApplication={onOpenApplication} />
          <OpenWorkspaceAction onOpenApplication={onOpenApplication} />
        </section>
      </section>
    </main>
  )
}
