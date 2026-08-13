import { Layout } from 'antd'
import type { ReactElement } from 'react'
import type {
  ApplicationConfig,
  ApplicationLifecycle,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningPageTreeNode,
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
  developmentPlanningPageTree: DevelopmentPlanningPageTreeNode[]
  developmentPlanningApiContracts: DevelopmentPlanningApiContract[]
  editorMode: EditorMode
  onApplicationUpdate: (application: ApplicationConfig) => void
  onApplicationLifecycleChange: (lifecycle: ApplicationLifecycle) => void
  onPlanningArtifactsRefresh: () => void
  previewBaseUrl: string
  previewLaunchError: string
  rightPanelOpen: boolean
  onRightPanelOpenChange: (open: boolean) => void
  applicationPreviewMode: boolean
  onApplicationPreviewModeChange: (open: boolean) => void
  theme: 'light' | 'dark'
  versionReadOnly: boolean
  versionPreviewOnly: boolean
  versionViewKey: string
}

/** 组合工作台左侧应用导航与主 Workflow 面板。 */
export default function LeftPanel({
  application,
  applicationLifecycle,
  developmentPlanningReady,
  hasPageDesigns,
  developmentPlanningPages,
  developmentPlanningPageTree,
  developmentPlanningApiContracts,
  editorMode,
  onApplicationUpdate,
  onApplicationLifecycleChange,
  onPlanningArtifactsRefresh,
  previewBaseUrl,
  previewLaunchError,
  rightPanelOpen,
  onRightPanelOpenChange,
  applicationPreviewMode,
  onApplicationPreviewModeChange,
  theme,
  versionReadOnly,
  versionPreviewOnly,
  versionViewKey
}: Props): ReactElement {
  return (
    <div className={cx('left-panel-wrapper')}>
      <Sider width="100%" className={cx('workbench-pane', 'workbench-left')}>
        <div className={cx('pane-content')}>
          <AiChatPanel
            key={versionViewKey}
            application={application}
            applicationLifecycle={applicationLifecycle}
            developmentPlanningReady={developmentPlanningReady}
            hasPageDesigns={hasPageDesigns}
            developmentPlanningPages={developmentPlanningPages}
            developmentPlanningPageTree={developmentPlanningPageTree}
            developmentPlanningApiContracts={developmentPlanningApiContracts}
            editorMode={editorMode}
            onApplicationUpdate={onApplicationUpdate}
            onApplicationLifecycleChange={onApplicationLifecycleChange}
            onPlanningArtifactsRefresh={onPlanningArtifactsRefresh}
            previewBaseUrl={previewBaseUrl}
            previewLaunchError={previewLaunchError}
            theme={theme}
            rightPanelOpen={rightPanelOpen}
            onRightPanelOpenChange={onRightPanelOpenChange}
            applicationPreviewMode={applicationPreviewMode}
            onApplicationPreviewModeChange={onApplicationPreviewModeChange}
            versionReadOnly={versionReadOnly}
            versionPreviewOnly={versionPreviewOnly}
            versionViewKey={versionViewKey}
          />
        </div>
      </Sider>
    </div>
  )
}
