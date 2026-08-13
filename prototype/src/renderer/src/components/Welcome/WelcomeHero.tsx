import { cx } from '../../utils'

export default function WelcomeHero(): JSX.Element {
  return (
    <header className={cx('welcome-hero')}>
      <h1>从想法到可运行代码</h1>
      <p>设计、开发、审查，在同一个本地工作区内完成。</p>
    </header>
  )
}
