import { cx } from '../../utils'

export default function WelcomeHero(): JSX.Element {
  return (
    <header className={cx('welcome-hero')}>
      <h1>从想法到可运行代码</h1>
      <p>需求分析、项目规划、开发阶段、测试阶段与审查阶段，在同一个本地工作区内完成。</p>
    </header>
  )
}
