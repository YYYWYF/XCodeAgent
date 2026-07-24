import { Layout } from 'antd'
import type { ReactElement } from 'react'
import type {
  ApplicationConfig,
  ApplicationLifecycle,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningPageOption,
  EditorMode
} from '../../typings'
import { cx } from '../../utils'
import AiChatPanel from '../AiChatPanel'
import './LeftPanel.less'

const { Sider } = Layout

type Props = {
  application: ApplicationConfig
  applicationLifecycle?: ApplicationLifecycle
  developmentPlanningReady: boolean
  hasPageDesigns: boolean
  developmentPlanningPages: DevelopmentPlanningPageOption[]
  developmentPlanningApiContracts: DevelopmentPlanningApiContract[]
  editorMode: EditorMode
  onApplicationUpdate: (application: ApplicationConfig) => void
  onApplicationLifecycleChange: (lifecycle: ApplicationLifecycle) => void
  onPlanningArtifactsRefresh: () => void
  onReturnWelcome: () => void
  onThemeChange: (theme: 'light' | 'dark') => void
  theme: 'light' | 'dark'
}

/** 组合工作台左侧应用导航与主 Workflow 面板。 */
export default function LeftPanel({
  application,
  applicationLifecycle,
  developmentPlanningReady,
  hasPageDesigns,
  developmentPlanningPages,
  developmentPlanningApiContracts,
  editorMode,
  onApplicationUpdate,
  onApplicationLifecycleChange,
  onPlanningArtifactsRefresh,
  onReturnWelcome,
  onThemeChange,
  theme
}: Props): ReactElement {
  return (
    <div className={cx('left-panel-wrapper')}>
      <Sider width="100%" className={cx('workbench-pane', 'workbench-left')}>
        <div className={cx('pane-content')}>
          <AiChatPanel
            application={application}
            applicationLifecycle={applicationLifecycle}
            developmentPlanningReady={developmentPlanningReady}
            hasPageDesigns={hasPageDesigns}
            developmentPlanningPages={developmentPlanningPages}
            developmentPlanningApiContracts={developmentPlanningApiContracts}
            editorMode={editorMode}
            onApplicationUpdate={onApplicationUpdate}
            onApplicationLifecycleChange={onApplicationLifecycleChange}
            onPlanningArtifactsRefresh={onPlanningArtifactsRefresh}
            onReturnWelcome={onReturnWelcome}
            onThemeChange={onThemeChange}
            theme={theme}
          />
        </div>
      </Sider>
    </div>
  )
}
