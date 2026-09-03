import { QuestionCircleOutlined, SettingOutlined } from '@ant-design/icons'
import { Button, message, Tooltip } from 'antd'
import {
  CreateApplicationAction,
  OpenWorkspaceAction,
  WelcomeAgentTrack,
  WelcomeHero,
  WelcomeRecentProjects
} from '../components/Welcome'
import type { ApplicationConfig, ApplicationLifecycle } from '../typings'
import { cx } from '../utils'
import './WelcomePage.less'
import './WelcomePageLight.less'

type Props = {
  onBeforeDeleteApplication: (application: ApplicationConfig) => Promise<void>
  onOpenApplication: (application: ApplicationConfig) => void
  onStartPlanning: (
    application: ApplicationConfig,
    threadId: string,
    lifecycle: ApplicationLifecycle
  ) => void
  theme: WelcomeTheme
}

type WelcomeTheme = 'dark' | 'light'

// 渲染首页，并以统一项目列表承载所有设计、规划和开发阶段的应用入口。
export default function WelcomePage({
  onBeforeDeleteApplication,
  onOpenApplication,
  onStartPlanning,
  theme
}: Props): JSX.Element {
  return (
    <main className={cx('welcome-page')} data-theme={theme}>
      <section className={cx('welcome-shell')}>
        <header className={cx('welcome-topbar')}>
          <div className={cx('welcome-brand')} aria-label="XCodeAgent">
            <span className={cx('welcome-brand-mark')} aria-hidden="true">
              <i />
              <i />
            </span>
            <span>XCodeAgent</span>
          </div>

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
              <CreateApplicationAction onStartPlanning={onStartPlanning} theme={theme} />
              <OpenWorkspaceAction onOpenApplication={onOpenApplication} theme={theme} />
            </section>

            <WelcomeRecentProjects
              onBeforeDeleteApplication={onBeforeDeleteApplication}
              onOpenApplication={onOpenApplication}
              theme={theme}
            />
          </section>

          <WelcomeAgentTrack />
        </div>
      </section>
    </main>
  )
}
