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
import type {
  RequirementSpecDraftSaveResult,
  WorkflowRevisionContinuationHandoff
} from '../../service/applicationPagePlanning'
import type { ApplicationPlanningCurrentState } from '../../service/activeApplicationPlanning'
import { cx } from '../../utils'
import AiChatPanel from '../AiChatPanel'
import './LeftPanel.less'

const { Sider } = Layout

type Props = {
  application: ApplicationConfig
  applicationLifecycle?: ApplicationLifecycle
  developmentTotals?: { completed: number; total: number }
  developmentPlanningReady: boolean
  developmentPlanningPages: DevelopmentPlanningPageOption[]
  developmentPlanningPageTree: DevelopmentPlanningPageTreeNode[]
  developmentPlanningApiContracts: DevelopmentPlanningApiContract[]
  developmentPlanningEntities: DevelopmentPlanningEntityOption[]
  editorMode: EditorMode
  onApplicationUpdate: (application: ApplicationConfig) => void
  /** 把应用配置写回 application.json（见 AiChatPanel 的 onPersistApplication）。 */
  onPersistApplication?: (application: ApplicationConfig) => Promise<void> | void
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
    requirementSpecFeedback?: string,
    designChangeRequest?: string
  ) => Promise<void>
  onStartDesignStageRevision: (input: WorkflowDesignStageRevisionStart) => Promise<void>
  onStartIterationPlanning: (request: string) => Promise<void>
  onRevisionContinuationHandlerChange: (
    handler?: (handoff: WorkflowRevisionContinuationHandoff) => Promise<void>
  ) => void
  onThemeChange: (theme: 'light' | 'dark') => void
  onPlanningStreamReady?: (
    inject: ((chunk: { content?: string; workflow?: WorkflowRunPayload }) => void) | null
  ) => void
  onSavePlanningRequirementSpec: (
    spec: Record<string, unknown>
  ) => Promise<RequirementSpecDraftSaveResult>
  onStopPlanning: () => Promise<void>
  onSessionHistoryReadyChange: (ready: boolean, error?: string) => void
  /** 当前应用是否正在生成模板（驱动前端加载态卡片）。 */
  generatingTemplate?: boolean
  /** 从工作台错误卡片重试设计阶段规划任务。 */
  onRetryPlanning?: () => void
  /** 通过专用动作重试失败的模板能力更新。 */
  onRetryTemplateReconcile?: () => void
  /** 当前应用唯一的 Planning 业务状态。 */
  planningState?: ApplicationPlanningCurrentState
  theme: 'light' | 'dark'
  rightPanelOpen: boolean
  /** 正在查看历史分支：对话区改为只读的应用文件/应用预览双 tab。 */
  versionReadOnly?: boolean
  /** 所查看历史分支的分支名：应用文件与预览按它读取该分支当时的内容。 */
  viewedBranchName?: string
  onRightPanelOpenChange: (open: boolean) => void
}

/** 组合工作台左侧应用导航与主 Workflow 面板。 */
export default function LeftPanel({
  application,
  applicationLifecycle,
  developmentTotals,
  developmentPlanningReady,
  developmentPlanningPages,
  developmentPlanningPageTree,
  developmentPlanningApiContracts,
  developmentPlanningEntities,
  editorMode,
  onApplicationUpdate,
  onPersistApplication,
  onApplicationLifecycleChange,
  onPlanningArtifactsRefresh,
  previewBaseUrl,
  previewLaunchError,
  previewLaunchLoading,
  onReturnWelcome,
  onSubmitPlanningClarification,
  onStartDesignStageRevision,
  onStartIterationPlanning,
  onRevisionContinuationHandlerChange,
  onThemeChange,
  onPlanningStreamReady,
  onSavePlanningRequirementSpec,
  onStopPlanning,
  onSessionHistoryReadyChange,
  generatingTemplate,
  onRetryPlanning,
  onRetryTemplateReconcile,
  planningState,
  theme,
  rightPanelOpen,
  versionReadOnly = false,
  viewedBranchName,
  onRightPanelOpenChange
}: Props): ReactElement {
  return (
    <div className={cx('left-panel-wrapper')}>
      <Sider width="100%" className={cx('workbench-pane', 'workbench-left')}>
        <div className={cx('pane-content')}>
          <AiChatPanel
            application={application}
            applicationLifecycle={applicationLifecycle}
            developmentTotals={developmentTotals}
            developmentPlanningReady={developmentPlanningReady}
            developmentPlanningPages={developmentPlanningPages}
            developmentPlanningPageTree={developmentPlanningPageTree}
            developmentPlanningApiContracts={developmentPlanningApiContracts}
            developmentPlanningEntities={developmentPlanningEntities}
            editorMode={editorMode}
            onApplicationUpdate={onApplicationUpdate}
            onPersistApplication={onPersistApplication}
            onApplicationLifecycleChange={onApplicationLifecycleChange}
            onPlanningArtifactsRefresh={onPlanningArtifactsRefresh}
            previewBaseUrl={previewBaseUrl}
            previewLaunchError={previewLaunchError}
            previewLaunchLoading={previewLaunchLoading}
            onReturnWelcome={onReturnWelcome}
            onSubmitPlanningClarification={onSubmitPlanningClarification}
            onStartDesignStageRevision={onStartDesignStageRevision}
            onStartIterationPlanning={onStartIterationPlanning}
            onRevisionContinuationHandlerChange={onRevisionContinuationHandlerChange}
            onThemeChange={onThemeChange}
            onPlanningStreamReady={onPlanningStreamReady}
            onSavePlanningRequirementSpec={onSavePlanningRequirementSpec}
            onStopPlanning={onStopPlanning}
            onSessionHistoryReadyChange={onSessionHistoryReadyChange}
            generatingTemplate={generatingTemplate}
            onRetryPlanning={onRetryPlanning}
            onRetryTemplateReconcile={onRetryTemplateReconcile}
            planningState={planningState}
            theme={theme}
            rightPanelOpen={rightPanelOpen}
            onRightPanelOpenChange={onRightPanelOpenChange}
            versionReadOnly={versionReadOnly}
            viewedBranchName={viewedBranchName}
          />
        </div>
      </Sider>
    </div>
  )
}
