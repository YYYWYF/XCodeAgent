import { Layout } from 'antd'
import type { ReactElement } from 'react'
import type {
  ApplicationConfig,
  ApplicationLifecycle,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningEntity,
  DevelopmentPlanningPageOption,
  DevelopmentPlanningPageTreeNode,
  EditorMode
} from '../../typings'
import type { WorkbenchArtifactProgress } from '../../workbenchDomain'
import type { BackgroundTaskSystem } from '../../backgroundTasks'
import { cx } from '../../utils'
import type {
  TestCaseGenerationTaskType,
  TestCasePreparationSnapshot
} from '../../testCasePreparation'
import AiChatPanel from '../AiChatPanel'
import type { ConversationManagementContent } from '../AiChatPanel/components/AuxiliaryDrawer'
import './LeftPanel.less'

const { Sider } = Layout

type Props = {
  application: ApplicationConfig
  applicationLifecycle?: ApplicationLifecycle
  developmentPlanningReady: boolean
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
  versionReadOnly: boolean
  versionPreviewOnly: boolean
  versionViewKey: string
  /** 顶部阶段条请求打开“进入测试”确认弹框的自增信号（透传给聊天面板）。 */
  testingEntryRequest?: number
  /** 聊天面板上报测试阶段是否具备进入条件（透传给工作台页）。 */
  onTestingEntryAvailableChange?: (available: boolean) => void
  /** 顶部阶段条请求重新唤起“进入开发”准入门弹框的自增信号（透传给聊天面板）。 */
  developmentEntryRequest?: number
  /** 聊天面板上报开发准入门是否待处理（透传给工作台页）。 */
  onDevelopmentEntryAvailableChange?: (available: boolean) => void
  /** 顶部阶段条请求重新唤起“进入项目规划”准入门弹框的自增信号（透传给聊天面板）。 */
  planningEntryRequest?: number
  /** 聊天面板上报项目规划准入门是否待处理（透传给工作台页）。 */
  onPlanningEntryAvailableChange?: (available: boolean) => void
  /** 顶部阶段条请求打开“进入审查”确认弹框的自增信号（透传给聊天面板）。 */
  reviewEntryRequest?: number
  /** 聊天面板上报测试通过后是否具备进入审查条件（透传给工作台页）。 */
  onReviewEntryAvailableChange?: (available: boolean) => void
  /** 聊天面板上报开发产物完成进度（透传给顶部阶段条）。 */
  onDevelopmentArtifactProgressChange?: (progress: WorkbenchArtifactProgress) => void
  testCasePreparation: TestCasePreparationSnapshot
  testPreparationOpenRequest: number
  onRetryTestCases: () => void
  /** 后台任务队列抽屉是否展开（透传给聊天面板左侧菜单）。 */
  backgroundTasksDrawer?: BackgroundTaskSystem | null
  /** 左侧菜单切换后台任务队列抽屉（工作台页统一处理互斥）。 */
  onOpenBackgroundTasks?: (system: BackgroundTaskSystem) => void
  /** 点击待验收任务的「验收」入口后请求聊天面板启动验收工作流（自增请求）。 */
  backgroundTaskAcceptRequest?: { nonce: number; taskId: string }
  /** 输入区请求继续后台工作流；工作台页统一处理任务锁与请求序号。 */
  onRequestBackgroundTaskContinuation?: (taskId: string) => void
  /** 验收工作流结束（无论成败）后回调；工作台页据此解除其它入口的禁用态。 */
  onBackgroundTaskAcceptanceSettled?: (taskId: string) => void
  /** 左侧菜单打开任务管理抽屉（工作台页统一处理互斥）。 */
  onOpenConversationManagement?: () => void
  /** 任务管理抽屉是否展开（透传给聊天面板菜单激活态）。 */
  conversationDrawerOpen?: boolean
  /** 聊天面板注册任务管理内容查询函数（透传给工作台页）。 */
  onConversationManagementReady?: (query: () => ConversationManagementContent) => void
  /** 关闭辅助抽屉（透传给聊天面板）。 */
  onCloseAuxiliaryDrawer?: () => void
  /** 关闭辅助抽屉（透传给聊天面板）。 
  onCloseAuxiliaryDrawer?: () => void
  /** 项目计划确认时同步所选的测试用例生成任务类型。 */
  onTestCaseGenerationTaskTypeChange?: (taskType: TestCaseGenerationTaskType) => void
}

/** 组合工作台左侧应用导航与主 Workflow 面板。 */
export default function LeftPanel({
  application,
  applicationLifecycle,
  developmentPlanningReady,
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
  versionReadOnly,
  versionPreviewOnly,
  versionViewKey,
  testingEntryRequest,
  onTestingEntryAvailableChange,
  developmentEntryRequest,
  onDevelopmentEntryAvailableChange,
  planningEntryRequest,
  onPlanningEntryAvailableChange,
  reviewEntryRequest,
  onReviewEntryAvailableChange,
  onDevelopmentArtifactProgressChange,
  testCasePreparation,
  testPreparationOpenRequest,
  onRetryTestCases,
  backgroundTasksDrawer,
  onOpenBackgroundTasks,
  backgroundTaskAcceptRequest,
  onRequestBackgroundTaskContinuation,
  onBackgroundTaskAcceptanceSettled,
  onOpenConversationManagement,
  conversationDrawerOpen,
  onConversationManagementReady,
  onCloseAuxiliaryDrawer,
  onTestCaseGenerationTaskTypeChange
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
            versionReadOnly={versionReadOnly}
            versionPreviewOnly={versionPreviewOnly}
            versionViewKey={versionViewKey}
            testingEntryRequest={testingEntryRequest}
            onTestingEntryAvailableChange={onTestingEntryAvailableChange}
            developmentEntryRequest={developmentEntryRequest}
            onDevelopmentEntryAvailableChange={onDevelopmentEntryAvailableChange}
            planningEntryRequest={planningEntryRequest}
            onPlanningEntryAvailableChange={onPlanningEntryAvailableChange}
            reviewEntryRequest={reviewEntryRequest}
            onReviewEntryAvailableChange={onReviewEntryAvailableChange}
            onDevelopmentArtifactProgressChange={onDevelopmentArtifactProgressChange}
            testCasePreparation={testCasePreparation}
            testPreparationOpenRequest={testPreparationOpenRequest}
            onRetryTestCases={onRetryTestCases}
            backgroundTasksDrawer={backgroundTasksDrawer}
            onOpenBackgroundTasks={onOpenBackgroundTasks}
            backgroundTaskAcceptRequest={backgroundTaskAcceptRequest}
            onRequestBackgroundTaskContinuation={onRequestBackgroundTaskContinuation}
            onBackgroundTaskAcceptanceSettled={onBackgroundTaskAcceptanceSettled}
            onOpenConversationManagement={onOpenConversationManagement}
            conversationDrawerOpen={conversationDrawerOpen}
            onConversationManagementReady={onConversationManagementReady}
            onCloseAuxiliaryDrawer={onCloseAuxiliaryDrawer}
            onTestCaseGenerationTaskTypeChange={onTestCaseGenerationTaskTypeChange}
          />
        </div>
      </Sider>
    </div>
  )
}
