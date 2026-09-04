import { QuestionCircleOutlined, SettingOutlined } from '@ant-design/icons'
import { Button, message, Tooltip } from 'antd'
import {
  CreateApplicationAction,
  OpenWorkspaceAction,
  WelcomeAgentTrack,
  WelcomeHero,
  WelcomeRecentProjects
} from '../components/Welcome'
import BrandLogo from '../components/BrandLogo'
import type { ApplicationConfig, ApplicationLifecycle } from '../typings'
import { cx } from '../utils'
import './WelcomePage.less'
import './WelcomePageLight.less'

type Props = {
  onOpenApplication: (application: ApplicationConfig) => void
  onOpenWorkbenchAfterCreate: (
    application: ApplicationConfig,
    lifecycle: ApplicationLifecycle
  ) => void
}

// 渲染首页。新建应用直接进工作台需求分析阶段（需求确认/项目规划在工作台内完成），
// 不再有独立的“未完成应用计划”恢复入口（原“阶段 1/2”分支已移除）。
export default function WelcomePage({
  onOpenApplication,
  onOpenWorkbenchAfterCreate
}: Props): JSX.Element {
  return (
    <main className={cx('welcome-page')} data-theme="light">
      <section className={cx('welcome-shell')}>
        <header className={cx('welcome-topbar')}>
          <BrandLogo className={cx('welcome-brand')} size={34} />

          <nav className={cx('welcome-utilities')} aria-label="欢迎页工具">
            <Button
              aria-label="设置"
              disabled
              icon={<SettingOutlined />}
              title="设置"
              type="text"
            />
            <Tooltip title="帮助功能即将推出">
              <Button
                aria-label="帮助"
                icon={<QuestionCircleOutlined />}
                onClick={() => message.info('帮助功能即将推出')}
                type="text"
              />
            </Tooltip>
          </nav>
        </header>

        <div className={cx('welcome-content')}>
          <section className={cx('welcome-primary')}>
            <WelcomeHero />

            <section className={cx('welcome-actions')} aria-label="开始使用 XCodeAgent">
              <CreateApplicationAction
                onOpenWorkbenchAfterCreate={onOpenWorkbenchAfterCreate}
              />
              <OpenWorkspaceAction onOpenApplication={onOpenApplication} />
            </section>

            <WelcomeRecentProjects onOpenApplication={onOpenApplication} />
          </section>

          <WelcomeAgentTrack />
        </div>
      </section>
    </main>
  )
}
