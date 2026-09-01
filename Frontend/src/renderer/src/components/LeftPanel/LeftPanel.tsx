import { Layout } from 'antd'
import type { ReactElement } from 'react'
import type {
  ApplicationConfig,
  ApplicationLifecycle,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningEntityOption,
  DevelopmentPlanningPageTreeNode,
  DevelopmentPlanningPageOption,
  EditorMode,
  WorkflowClarificationAnswers,
  WorkflowDesignStageRevisionStart,
  WorkflowRunPayload
} from '../../typings'
import type { WorkflowRevisionContinuationHandoff } from '../../service/applicationPagePlanning'
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
  developmentPlanningEntities: DevelopmentPlanningEntityOption[]
  editorMode: EditorMode
  onApplicationUpdate: (application: ApplicationConfig) => void
  onApplicationLifecycleChange: (lifecycle: ApplicationLifecycle) => void
  onPlanningArtifactsRefresh: () => void
  previewBaseUrl: string
  previewLaunchError: string
  previewLaunchLoading: boolean
  onReturnWelcome: () => void
  onSubmitPlanningClarification: (
    workflow: WorkflowRunPayload,
    answers: WorkflowClarificationAnswers,
    editedRequirementSpec?: Record<string, unknown>,
    requirementSpecFeedback?: string
  ) => Promise<void>
  onStopPlanning: () => Promise<void>
  onStartDesignStageRevision: (input: WorkflowDesignStageRevisionStart) => Promise<void>
  onRevisionContinuationHandlerChange: (
    handler?: (handoff: WorkflowRevisionContinuationHandoff) => Promise<void>
  ) => void
  onThemeChange: (theme: 'light' | 'dark') => void
  onPlanningStreamReady?: (
    inject: ((chunk: { content?: string; workflow?: WorkflowRunPayload }) => void) | null
  ) => void
  onSessionHistoryReadyChange: (ready: boolean) => void
  /** 当前应用是否正在生成模板（驱动前端加载态卡片）。 */
  generatingTemplate?: boolean
  /** 设计阶段后台规划窗口的模型错误。 */
  planningError?: string
  /** 从工作台错误卡片重新打开设计阶段规划窗口。 */
  onRetryPlanning?: () => void
  planningThreadId?: string
  /** 规划阶段新窗口使用的独立前端会话 threadId。 */
  planningConversationThreadId?: string
  planningWorkflow?: WorkflowRunPayload
  /** 仅冷恢复时允许从 .xcodeagent 读取当前阶段规划产物。 */
  restorePlanningArtifactsFromDisk?: boolean
  theme: 'light' | 'dark'
  rightPanelOpen: boolean
  onRightPanelOpenChange: (open: boolean) => void
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
  previewLaunchLoading,
  onReturnWelcome,
  onSubmitPlanningClarification,
  onStopPlanning,
  onStartDesignStageRevision,
  onRevisionContinuationHandlerChange,
  onThemeChange,
  onPlanningStreamReady,
  onSessionHistoryReadyChange,
  generatingTemplate,
  planningError,
  onRetryPlanning,
  planningThreadId,
  planningConversationThreadId,
  planningWorkflow,
  restorePlanningArtifactsFromDisk,
  theme,
  rightPanelOpen,
  onRightPanelOpenChange
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
            developmentPlanningPageTree={developmentPlanningPageTree}
            developmentPlanningApiContracts={developmentPlanningApiContracts}
            developmentPlanningEntities={developmentPlanningEntities}
            editorMode={editorMode}
            onApplicationUpdate={onApplicationUpdate}
            onApplicationLifecycleChange={onApplicationLifecycleChange}
            onPlanningArtifactsRefresh={onPlanningArtifactsRefresh}
            previewBaseUrl={previewBaseUrl}
            previewLaunchError={previewLaunchError}
            previewLaunchLoading={previewLaunchLoading}
            onReturnWelcome={onReturnWelcome}
            onSubmitPlanningClarification={onSubmitPlanningClarification}
            onStopPlanning={onStopPlanning}
            onStartDesignStageRevision={onStartDesignStageRevision}
            onRevisionContinuationHandlerChange={onRevisionContinuationHandlerChange}
            onThemeChange={onThemeChange}
            onPlanningStreamReady={onPlanningStreamReady}
            onSessionHistoryReadyChange={onSessionHistoryReadyChange}
            generatingTemplate={generatingTemplate}
            planningError={planningError}
            onRetryPlanning={onRetryPlanning}
            planningThreadId={planningThreadId}
            planningConversationThreadId={planningConversationThreadId}
            planningWorkflow={planningWorkflow}
            restorePlanningArtifactsFromDisk={restorePlanningArtifactsFromDisk}
            theme={theme}
            rightPanelOpen={rightPanelOpen}
            onRightPanelOpenChange={onRightPanelOpenChange}
          />
        </div>
      </Sider>
    </div>
  )
}
