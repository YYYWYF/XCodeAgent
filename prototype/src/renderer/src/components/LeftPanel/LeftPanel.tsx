import { Layout } from 'antd'
import type { ReactElement } from 'react'
import type {
  ApplicationConfig,
  ApplicationLifecycle,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningEntity,
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
  developmentPlanningEntities: DevelopmentPlanningEntity[]
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
  versionReadOnly: boolean
  versionPreviewOnly: boolean
  versionViewKey: string
  /** 顶部阶段条请求打开“进入测试”确认弹框的自增信号（透传给聊天面板）。 */
  testingEntryRequest?: number
  /** 聊天面板上报测试阶段是否具备进入条件（透传给工作台页）。 */
  onTestingEntryAvailableChange?: (available: boolean) => void
  /** 顶部阶段条请求打开“进入审查”确认弹框的自增信号（透传给聊天面板）。 */
  reviewEntryRequest?: number
  /** 聊天面板上报测试通过后是否具备进入审查条件（透传给工作台页）。 */
  onReviewEntryAvailableChange?: (available: boolean) => void
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
  developmentPlanningEntities,
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
  versionReadOnly,
  versionPreviewOnly,
  versionViewKey,
  testingEntryRequest,
  onTestingEntryAvailableChange,
  reviewEntryRequest,
  onReviewEntryAvailableChange
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
            developmentPlanningEntities={developmentPlanningEntities}
            editorMode={editorMode}
            onApplicationUpdate={onApplicationUpdate}
            onApplicationLifecycleChange={onApplicationLifecycleChange}
            onPlanningArtifactsRefresh={onPlanningArtifactsRefresh}
            previewBaseUrl={previewBaseUrl}
            previewLaunchError={previewLaunchError}
            rightPanelOpen={rightPanelOpen}
            onRightPanelOpenChange={onRightPanelOpenChange}
            applicationPreviewMode={applicationPreviewMode}
            onApplicationPreviewModeChange={onApplicationPreviewModeChange}
            versionReadOnly={versionReadOnly}
            versionPreviewOnly={versionPreviewOnly}
            versionViewKey={versionViewKey}
            testingEntryRequest={testingEntryRequest}
            onTestingEntryAvailableChange={onTestingEntryAvailableChange}
            reviewEntryRequest={reviewEntryRequest}
            onReviewEntryAvailableChange={onReviewEntryAvailableChange}
          />
        </div>
      </Sider>
    </div>
  )
}
