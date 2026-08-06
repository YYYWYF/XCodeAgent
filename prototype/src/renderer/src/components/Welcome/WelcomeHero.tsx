import { cx } from '../../utils'

export default function WelcomeHero(): JSX.Element {
  return (
    <header className={cx('welcome-hero')}>
      <h1>从想法到可运行代码</h1>
      <p>规划、生成、验证，在同一个本地工作区内完成。</p>
    </header>
  )
}
