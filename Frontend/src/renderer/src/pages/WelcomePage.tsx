import { QuestionCircleOutlined, SettingOutlined } from '@ant-design/icons'
import { Button, message, Tooltip } from 'antd'
import {
  CreateApplicationAction,
  ActivePlanningAction,
  OpenWorkspaceAction,
  WelcomeAgentTrack,
  WelcomeHero,
  WelcomeRecentProjects
} from '../components/Welcome'
import type { ApplicationConfig, WorkflowRunPayload } from '../typings'
import type { ActivePlanningStatus } from '../service/activeApplicationPlanning'
import { cx } from '../utils'
import './WelcomePage.less'
import './WelcomePageLight.less'

type Props = {
  activePlanning?: ApplicationConfig
  activePlanningStatus?: ActivePlanningStatus
  activePlanningWorkflow?: WorkflowRunPayload
  onOpenApplication: (application: ApplicationConfig) => void
  onOpenPlanning: () => void
  onStartPlanning: (application: ApplicationConfig, threadId: string) => void
}

type WelcomeTheme = 'dark' | 'light'

const THEME_PREFERENCE_KEY = 'xcode-agent-theme-preference'

// 读取欢迎页主题偏好，缺省使用浅色主题。
function getTheme(): WelcomeTheme {
  const storedPreference = window.localStorage.getItem(THEME_PREFERENCE_KEY)
  return storedPreference === 'light' || storedPreference === 'dark' ? storedPreference : 'light'
}

// 渲染首页，并在存在未完成规划时显示唯一的恢复入口。
export default function WelcomePage({
  activePlanning,
  activePlanningStatus,
  activePlanningWorkflow,
  onOpenApplication,
  onOpenPlanning,
  onStartPlanning
}: Props): JSX.Element {
  const theme = getTheme()

  return (
    <main
      className={cx('welcome-page', activePlanning && 'has-active-planning')}
      data-theme={theme}
    >
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
            <Button aria-label="设置" disabled icon={<SettingOutlined />} title="设置" type="text" />
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
                disabled={activePlanningStatus === 'running' || activePlanningStatus === 'error'}
                onStartPlanning={onStartPlanning}
                theme={theme}
              />
              <OpenWorkspaceAction onOpenApplication={onOpenApplication} theme={theme} />
            </section>

            {activePlanning ? (
              <ActivePlanningAction
                application={activePlanning}
                onOpen={onOpenPlanning}
                status={activePlanningStatus || 'running'}
                workflow={activePlanningWorkflow}
              />
            ) : null}

            <WelcomeRecentProjects onOpenApplication={onOpenApplication} />
          </section>

          <WelcomeAgentTrack />
        </div>
      </section>
    </main>
  )
}
