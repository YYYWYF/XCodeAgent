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
import type { ApplicationConfig, ApplicationLifecycle } from '../typings'
import {
  MAX_ACTIVE_APPLICATION_PLANS,
  type PersistedActivePlanning
} from '../service/activeApplicationPlanning'
import { cx } from '../utils'
import './WelcomePage.less'
import './WelcomePageLight.less'

type Props = {
  activePlannings: PersistedActivePlanning[]
  deletingPlanningIds: ReadonlySet<string>
  onDeletePlanning: (applicationId: string) => void
  onOpenApplication: (application: ApplicationConfig) => void
  onOpenPlanning: (applicationId: string) => void
  onStartPlanning: (
    application: ApplicationConfig,
    threadId: string,
    lifecycle: ApplicationLifecycle
  ) => void
  theme: WelcomeTheme
}

type WelcomeTheme = 'dark' | 'light'

// 渲染首页，并为每个未完成规划显示相互隔离的恢复入口。
export default function WelcomePage({
  activePlannings,
  deletingPlanningIds,
  onDeletePlanning,
  onOpenApplication,
  onOpenPlanning,
  onStartPlanning,
  theme
}: Props): JSX.Element {
  return (
    <main
      className={cx('welcome-page', activePlannings.length > 0 && 'has-active-planning')}
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
                activePlanningCount={activePlannings.length}
                disabled={activePlannings.length >= MAX_ACTIVE_APPLICATION_PLANS}
                onStartPlanning={onStartPlanning}
                theme={theme}
              />
              <OpenWorkspaceAction onOpenApplication={onOpenApplication} theme={theme} />
            </section>

            {activePlannings.length > 0 ? (
              <section className={cx('active-planning-list')} aria-label="未完成的应用计划">
                {activePlannings.map((planning) => (
                  <ActivePlanningAction
                    application={planning.application}
                    deleting={deletingPlanningIds.has(planning.application.id)}
                    key={planning.application.id}
                    lifecycle={planning.lifecycle}
                    onDelete={() => onDeletePlanning(planning.application.id)}
                    onOpen={() => onOpenPlanning(planning.application.id)}
                    status={planning.status}
                  />
                ))}
              </section>
            ) : null}

            <WelcomeRecentProjects onOpenApplication={onOpenApplication} theme={theme} />
          </section>

          <WelcomeAgentTrack />
        </div>
      </section>
    </main>
  )
}
