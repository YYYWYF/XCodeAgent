import { QuestionCircleOutlined, SettingOutlined } from '@ant-design/icons'
import { Button, message, Tooltip } from 'antd'
import {
  CreateApplicationAction,
  OpenWorkspaceAction,
  WelcomeHero,
  WelcomeRecentProjects
} from '../components/Welcome'
import BrandLogo from '../components/BrandLogo'
import heroArtworkLight from '../assets/welcome-application-layers-light.png'
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
  theme: WelcomeTheme
}

type WelcomeTheme = 'dark' | 'light'

// 渲染首页。新建应用直接进工作台设计阶段（需求确认/项目规划在工作台内完成），
// 不再有独立的“未完成应用计划”恢复入口（原“阶段 1/2”分支已移除）。
export default function WelcomePage({
  onOpenApplication,
  onOpenWorkbenchAfterCreate
}: Props): JSX.Element {
  return (
    <main className={cx('welcome-page')} data-theme="light">
      <section className={cx('welcome-shell')}>
        <header className={cx('welcome-topbar')}>
          <BrandLogo className="welcome-brand" size={34} />

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
          <section className={cx('welcome-create-area')}>
            <div className={cx('welcome-hero-stage')}>
              <div className={cx('welcome-hero-copy')}>
                <WelcomeHero />
              </div>
              <div className={cx('welcome-hero-artwork')} aria-hidden="true">
                <img className={cx('welcome-artwork-light')} src={heroArtworkLight} alt="" />
              </div>
            </div>
            <CreateApplicationAction
              onOpenWorkbenchAfterCreate={onOpenWorkbenchAfterCreate}
              theme="light"
            />
          </section>

          <section className={cx('welcome-lower')}>
            <WelcomeRecentProjects
              headerAction={
                <OpenWorkspaceAction compact onOpenApplication={onOpenApplication} theme="light" />
              }
              onOpenApplication={onOpenApplication}
              theme="light"
            />
          </section>
        </div>
      </section>
    </main>
  )
}
