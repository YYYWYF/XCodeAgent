import { useWorkflowDocuments } from './useWorkflowDocuments'
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  PauseCircleOutlined
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Checkbox,
  Input,
  Radio,
  Tag,
  Tooltip,
  Typography
} from 'antd'
import type { ReactElement } from 'react'
import { useEffect, useMemo, useState } from 'react'
import type {
  UiDesignIterationOrigin,
  WorkflowBuildExecutionSlice,
  WorkflowClarification,
  WorkflowClarificationQuestion,
  WorkflowClarificationSelectionGroup,
  WorkflowClarificationAnswer,
  WorkflowClarificationAnswers,
  WorkflowBuildTaskPlan,
  WorkflowBuildTaskPlanConfirmation,
  WorkflowConfirmationArtifact,
  WorkflowCodeReviewResult,
  WorkflowCodeReviewRepair,
  WorkflowCodeReviewScan,
  WorkflowRevisionDraft,
  WorkflowRevisionDraftInteraction,
  WorkflowRevisionImpact,
  WorkflowTestTarget,
  WorkflowRunPayload,
  ApplicationConfig
} from '../../../../typings'
import { cx } from '../../../../utils'
import {
  entityDesignActionContinuationMessage,
  pageAcceptanceContinuationMessage
} from '../../workflowContinuation'
import type { WorkflowInteractionAvailability } from '../../planExecutionMode'
import { workflowShouldShowCodeReview, workflowShouldShowProjectLaunch } from '../../utils'
import { useWorkbenchPhase } from '../../../../context'
import AgentApprovalCard from '../AgentApprovalCard'
import { approveToolRequest, rejectToolRequest } from '../../../../service/workspaceTools'
import type { ToolApproval } from '../../../../service/workspaceTools'
import ConfirmationArtifact from './ConfirmationArtifact'
import RevisionImpactReview from '../ApplicationRevisionCard/RevisionImpactReview'
import RevisionDraftReview from '../ApplicationRevisionCard/RevisionDraftReview'
import { technicalPlanDiff } from '../DocPanel/technicalPlanDiff'
import { asRecord } from '../DocPanel/TechnicalPlanDocPanelData'
import BuildTaskPlanConfirmation from './BuildTaskPlanConfirmation'
import { BuildExecutionTaskList } from './BuildExecutionTaskList'
import { BuildTaskRetryNotice } from './BuildTaskRetryNotice'
import { sortBuildTasksForDisplay } from './buildExecutionPresentation'
import DetailReview from './DetailReview'
import ApiDesignReadinessGateCard from './ApiDesignReadinessGateCard'
import type { ApiDesignConfigTarget } from './ApiDesignConfigModal'
import EntityDesignGateCard from './EntityDesignGateCard'
import ProjectLaunchCard from './ProjectLaunchCard'
import PlanningStageEntryCard from './PlanningStageEntryCard'
import TestPhaseConfirmationCard from './TestPhaseConfirmationCard'
import DirectModificationScopeReview from './DirectModificationScopeReview'
import UnitTestConfirmationPanel from './UnitTestConfirmationPanel'
import FrontendPerformanceConfirmationPanel from './FrontendPerformanceConfirmationPanel'
import ReviewPhaseConfirmationCard from './ReviewPhaseConfirmationCard'
import AcceptancePhaseConfirmationCard from './AcceptancePhaseConfirmationCard'
import CodeReviewCard from './CodeReviewCard'
import { workflowClarification, workflowUnitTestRunning } from './workflowClarification'
import {
  buildTaskDisplayStatus,
  isModuleCheckpointCandidate
} from './buildTaskStatus'
import {
  bindDagConfirmationDraftIdentity,
  currentDagConfirmationDraftIdentity
} from '../../stageOutputState'
import UiDesignConfirmationPanel from '../../../Welcome/UiDesignConfirmationPanel'
import { PlanConfirmationCard } from './PlanConfirmationCard'
import ApplicationConfigPrerequisiteCard from './ApplicationConfigPrerequisiteCard'
import './WorkflowRunCard.less'

export { PlanConfirmationCard } from './PlanConfirmationCard'

const { Text } = Typography
const { TextArea } = Input

const OTHER_OPTION_VALUE = '__other__'

/** 校验门禁卡片的应用、页面或 Endpoint 开发目标。 */
function normalizeApiDesignDevelopmentTarget(
  value: unknown
): { type: 'application' | 'page' | 'endpoint'; id: string; label?: string; apiContractId?: string } | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  const target = value as Record<string, unknown>
  const type = target.type === 'application' ? 'application' : target.type === 'page' ? 'page' : target.type === 'endpoint' ? 'endpoint' : undefined
  const id = String(target.id || '')
  if (!type || !id) return undefined
  return {
    type,
    id,
    label: String(target.label || '') || undefined,
    apiContractId: String(target.apiContractId || '') || undefined
  }
}

// 设计阶段产物确认卡 mode → 文档信息（驱动产物确认行渲染）。
const ARTIFACT_CONFIRMATION_MAP: Record<string, { title: string; summary: string }> = {
  requirement_document_confirmation: {
    title: '需求文档',
    summary: '需求文档已生成，确认后生成 UI 设计稿。'
  },
  technical_plan_confirmation: {
    title: '技术规划',
    summary: '技术规划已生成，确认后进入应用工作区。'
  },
  project_plan_confirmation: {
    title: '项目计划',
    summary: '项目计划已生成，确认后生成构建任务清单。'
  }
}

export type ClarificationAnswers = WorkflowClarificationAnswers

type WorkflowRunCardProps = {
  disabled?: boolean
  interactionAvailability: WorkflowInteractionAvailability
  workflowCardResult?: 'confirmed' | 'cancelled' | 'submitted'
  /** 已答完的历史澄清卡：header→答案 映射，存在时按原控件形态回填答案并以禁用态展示。 */
  historicalClarificationAnswers?: Record<string, string>
  /** 当前技术规划会话繁忙时禁用已确认卡上的修改入口。 */
  reviseConfirmedTechnicalPlanDisabled?: boolean
  onEntityDesignGateJump?: (entityId: string, workflow: WorkflowRunPayload) => void
  onOpenApiDesignConfig?: (target: ApiDesignConfigTarget, workflow: WorkflowRunPayload) => void
  onOpenTechnicalPlanChanges?: () => void
  /** 已确认的技术规划通过当前规划会话发起正式修订。 */
  onReviseConfirmedTechnicalPlan?: (request: string) => void | Promise<void>
  onSubmitClarification?: (
    workflow: WorkflowRunPayload,
    answers: ClarificationAnswers,
    editedRequirementSpec?: Record<string, unknown>
  ) => void | Promise<void>
  /** 用户在 UI 调整输入框里提出了页面级新增/删除，需要转交产品意图识别路径处理；
   *  参数是用户的原始指令文本。 */
  onTransferToProductAgent?: (instruction: string) => void | Promise<void>
  /** UI 设计稿确认：当前选中页 id（与右侧预览面板联动）。 */
  uiDesignActivePageId?: string
  /** UI 设计稿确认：选中页变化时通知外部（联动右侧预览）。 */
  onUiDesignActivePageChange?: (pageId: string) => void
  /** UI 设计稿确认：当前正在执行动作的 pageId 集合（联动右侧逐页加载态）。 */
  uiDesignActingPageIds?: string[]
  /** 当前门禁会话中已经保存、但尚未统一检测的 Endpoint 映射键。 */
  apiDesignSavedMappingKeys?: ReadonlySet<string>
  /** UI 设计稿确认：动作页集合变化时通知外部。 */
  onUiDesignActingPageIdsChange?: (ids: string[]) => void
  /** 需求文档确认：保存编辑草稿（重写 Markdown+JSON），返回更新后的 spec。 */
  onSaveRequirementSpec?: (
    workflow: WorkflowRunPayload,
    spec: Record<string, unknown>
  ) => Promise<Record<string, unknown> | undefined>
  /** 需求文档确认：菜单根路径（驱动编辑器页面路由前缀）。 */
  rootPath?: string
  workflow: WorkflowRunPayload
  workspaceRoot?: string
  application?: ApplicationConfig
}

/** 渲染 Workflow 交互卡片，并按已保存的操作结果恢复卡片状态。 */
export default function WorkflowRunCard({
  disabled,
  historicalClarificationAnswers,
  interactionAvailability,
  workflowCardResult,
  onEntityDesignGateJump,
  onOpenApiDesignConfig,
  onOpenTechnicalPlanChanges,
  onReviseConfirmedTechnicalPlan,
  reviseConfirmedTechnicalPlanDisabled,
  onSubmitClarification,
  onTransferToProductAgent,
  uiDesignActivePageId,
  onUiDesignActivePageChange,
  uiDesignActingPageIds,
  onUiDesignActingPageIdsChange,
  apiDesignSavedMappingKeys,
  onSaveRequirementSpec,
  rootPath,
  workflow: sourceWorkflow,
  workspaceRoot,
  application
}: WorkflowRunCardProps): ReactElement | null {
  const documents = useWorkflowDocuments(sourceWorkflow, workspaceRoot, !disabled)
  const workflow = documents.workflow
  const { phase: currentWorkbenchPhase } = useWorkbenchPhase()
  const status = String(workflow.summary.status || 'unknown')
  const clarification = workflowClarification(workflow)
  const unitTestRunning = workflowUnitTestRunning(workflow) && !clarification
  // 项目启动快照可能暂时保留上一测试节点已提交的性能测试确认；启动卡不应重复展示该旧交互。
  const projectLaunch = workflowShouldShowProjectLaunch(workflow, currentWorkbenchPhase)
  const launchNode = workflow.summary.phase === 'launch_project'
  const reviewPhaseConfirmation = clarification?.mode === 'review_phase_confirmation'
  const acceptancePhaseConfirmation = clarification?.mode === 'acceptance_phase_confirmation'
  const codeReviewResult = readCodeReviewResult(workflow)
  const codeReviewRepair = readCodeReviewRepair(workflow)
  const codeReviewScan = readCodeReviewScan(workflow)
  const codeReviewRepairConfirmation = clarification?.mode === 'code_review_repair_confirmation'
  // 恢复或重跑测试节点时快照可能短暂携带上一轮审查结果，必须以当前阶段为显示边界。
  const codeReview = workflowShouldShowCodeReview(workflow)
  const reviewStage = ['code_review', 'acceptance_phase_confirmation'].includes(
    String(workflow.summary.phase || '')
  )
  const staleFrontendPerformanceConfirmation =
    launchNode && clarification?.mode === 'frontend_performance_confirmation'
  const confirmationArtifact = workflowConfirmationArtifact(workflow, clarification)
  const clarificationQuestions = staleFrontendPerformanceConfirmation
    ? []
    : clarification?.questions || []
  const entityDesignGate = clarification?.mode === 'entity_source_binding_required'
  const apiDesignRequired = clarification?.mode === 'api_design_required'
  const apiDesignReadinessGate = apiDesignRequired
  const gateQuestion = clarification?.questions?.[0]
  const entityGateEntities = (clarification?.missing_entities || []).filter((item) =>
    Boolean(
      item &&
        typeof item === 'object' &&
        String((item as { entity_id?: string }).entity_id || '').trim()
    )
  )
  const detailReview =
    clarification?.mode === 'entity_source_binding' ? clarification.review : undefined
  // 实体设计评审：确认对象包含实体目标或实体设计摘要时，视为实体设计场景，
  // 此时裁剪卡片顶部的等待提示与“已生成产物”列表，避免与实体面板信息重复。
  const entityDesignReview = Boolean(
    detailReview &&
      ((detailReview.entities?.length || 0) > 0 || Boolean(detailReview.summary?.entityDesign))
  )
  const databaseApproval = workflowDatabaseApproval(clarification)
  const unitTestConfirmation = clarification?.mode === 'unit_test_confirmation'
  const frontendPerformanceConfirmation =
    clarification?.mode === 'frontend_performance_confirmation' &&
    !staleFrontendPerformanceConfirmation
  const revisionImpact = workflowRevisionImpact(workflow, clarification)
  const applicationConfigPrerequisite = clarification?.mode === 'application_config_prerequisite'
  const revisionDraftBinding = workflowRevisionDraftBinding(workflow, clarification)
  const dagConfirmation = clarification?.mode === 'build_task_plan_confirmation'
  const testPhaseConfirmation = clarification?.mode === 'test_phase_confirmation'
  const testTarget = (clarification?.testTarget ||
    workflow.summary?.testTarget ||
    workflow.state?.testTarget) as WorkflowTestTarget | undefined
  const dagTaskPlan = clarification?.taskPlan as WorkflowBuildTaskPlan | undefined
  // Confirm/Abandon/Regenerate 必须精确绑定服务端 DraftIdentity；缺失时禁止提交（fail closed）。
  const dagDraftIdentity = currentDagConfirmationDraftIdentity(workflow)
  const dagConfirmationErrors = dagDraftIdentity
    ? clarification?.errors
    : [
        ...(Array.isArray(clarification?.errors) ? clarification.errors : []),
        '当前待确认计划的确认信息不完整，请刷新后重试。'
      ]
  // 产物确认（需求文档/产品规划/UI设计/技术规划）：展示已生成与确认操作，不走通用表单。
  const artifactConfirmation = clarification?.mode
    ? ARTIFACT_CONFIRMATION_MAP[clarification.mode]
    : undefined
  const technicalPlanGenerationError = clarification?.mode === 'technical_plan_generation_error'
  const planningStageEntry = clarification?.mode === 'planning_stage_entry_confirmation'
  // UI 确认阶段：clarification.mode 或 summary.phase 判定。换一换/选模板期间流式快照
  // 可能短暂丢失 clarification.mode，用 phase=ui_confirmation 兜底，避免卡片闪烁切换。
  const uiDesignConfirmation =
    clarification?.mode === 'ui_design_confirmation' ||
    workflow.summary?.phase === 'ui_confirmation'
  const effectiveUiDesignWorkflow = workflow
  // 每个页面在历史迭代里的归属（后端按迭代记录的页面计划与产出事实），
  // 驱动设计稿卡片的「v1.0 已设计过」/「v1.0 该设计未设计」标注。
  // 读不到就传 undefined —— 面板退化成不标注，不会误标。
  const uiDesignIterationOrigins = useMemo(() => {
    const raw = (clarification as unknown as Record<string, unknown>)?.iteration_origins
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return undefined
    return raw as Record<string, UiDesignIterationOrigin>
  }, [clarification])
  // 创建规划各阶段生成中都要保留卡片，避免运行期间没有任何可见反馈。
  const planningPhase = workflow.summary?.phase
  const planningRunning =
    ['product_planning', 'project_planning', 'technical_planning'].includes(
      String(planningPhase)
    ) && status === 'running'
  // 各规划文档从 workflow.state/result 读取结构化内容，供确认卡预览。
  const productPlanObject = readProductPlan(workflow)
  const projectPlanObject = readProjectPlan(workflow)
  const technicalPlanObject = readTechnicalPlan(workflow)
  const technicalPlanBaseline = asRecord(workflow.state?.technical_plan_baseline)
  const technicalChanges = technicalPlanObject && Object.keys(technicalPlanBaseline).length
    ? technicalPlanDiff(technicalPlanBaseline, technicalPlanObject, productPlanObject || {}) : undefined
  const technicalChangeCount = technicalChanges?.reduce((sum, group) => sum + group.objects.length, 0)
  const technicalChangeSummary = technicalChanges
    ? technicalChangeCount ? `涉及 ${technicalChanges.filter((group) => group.objects.length).map((group) => `${group.objects.length} 个 ${group.name}对象`).join('、')}` : '本次未产生内容变化'
    : undefined
  // 技术规划弹框的「页面概览」版块需要上游 RequirementSpec 提取页面风格，故一并读取。
  const requirementSpecObject = readRequirementSpec(workflow)
  // 产物确认答案键必须与当前 mode 一一对应，避免确认产品规划时误发需求确认答案。
  const artifactAnswerKey =
    clarification?.mode === 'requirement_document_confirmation'
      ? 'requirement_document_confirmation'
      : clarification?.mode === 'technical_plan_confirmation'
        ? 'technical_plan_confirmation'
        : clarification?.mode === 'project_plan_confirmation'
          ? 'project_plan_confirmation'
          : 'requirement_document_confirmation'
  const databaseApprovalAnswerKey = clarificationQuestions[0]
    ? clarificationQuestionKey(clarificationQuestions[0], 0)
    : 'database_approval'
  const confirmationItemCount = detailReview
    ? (detailReview.pages?.length || 0) + (detailReview.endpoints?.length || 0)
    : dagConfirmation
      ? dagTaskPlan?.reviewTasks?.length || 0
      : testPhaseConfirmation
        ? 1
        : reviewPhaseConfirmation
          ? 1
          : acceptancePhaseConfirmation
            ? 1
            : uiDesignConfirmation
              ? (
                  (clarification as unknown as Record<string, unknown> | undefined)?.pages as
                    | unknown[]
                    | undefined
                )?.length || clarificationQuestions.length
              : clarificationQuestions.length
  const hasWorkflowCardResult = Boolean(workflowCardResult)
  const revisionImpactDecision =
    workflowCardResult === 'confirmed'
      ? 'approved'
      : workflowCardResult === 'cancelled'
        ? 'rejected'
        : undefined
  const hasRevisionImpactDecision = Boolean(revisionImpactDecision)
  const requiresConfirmation =
    !staleFrontendPerformanceConfirmation &&
    clarification?.status === 'requires_user_input' &&
    !hasWorkflowCardResult
  const activeConfirmation =
    requiresConfirmation && interactionAvailability === 'active'
  // Lifecycle 已判定过期的旧快照仍保留问题上下文，但不能继续标成当前待确认。
  const staleConfirmation =
    requiresConfirmation && interactionAvailability === 'stale'
  const workflowCardStatusVariant =
    workflowCardResult ||
    (staleConfirmation
      ? 'stale'
      : status === 'requires_user_input'
        ? 'pending'
        : undefined)
  const [answers, setAnswers] = useState<ClarificationAnswers>(() =>
    loadClarificationDraft(workflow.threadId, clarificationQuestions)
  )
  const canSubmitClarification =
    clarification?.status === 'requires_user_input' &&
    clarificationQuestions.length > 0 &&
    clarificationQuestions.every((question, index) =>
      clarificationAnswerComplete(question, answers[clarificationQuestionKey(question, index)])
    )
  const implementationFixCancelled =
    clarification?.mode === 'implementation_fix_confirmation' &&
    selectedAnswerValues(answers.implementation_fix_confirmation).includes('否')
  const updateAnswer = (key: string, value: WorkflowClarificationAnswer): void => {
    setAnswers((currentAnswers) => {
      const nextAnswers = { ...currentAnswers, [key]: value }
      saveClarificationDraft(workflow.threadId, clarificationQuestions, nextAnswers)
      return nextAnswers
    })
  }

  // 惰性初始化只在首次挂载执行一次；若挂载时 clarificationQuestions 尚未就绪（流式快照
  // 先到 running 后到 requires_user_input），answers 会初始化为空。这里在 questions 就绪
  // 且用户尚未填写时从 localStorage 补读草稿，覆盖恢复场景的时序竞态。
  const clarificationFingerprint = clarificationQuestions
    .map((question, index) => clarificationQuestionKey(question, index))
    .join('|')
  useEffect(() => {
    if (clarificationQuestions.length === 0) return
    setAnswers((current) => {
      if (current && Object.keys(current).length > 0) return current
      return loadClarificationDraft(workflow.threadId, clarificationQuestions)
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflow.threadId, clarificationFingerprint])

  // 生成失败统一由输入框上方的原底部区域展示，历史失败也不再创建空卡片。
  if (technicalPlanGenerationError) {
    return null
  }
  if (documents.loading) return <Alert type="info" showIcon message="正在读取确认资料…" />
  if (documents.error) return <Alert type="error" showIcon message="确认资料读取失败"
    description={documents.error} action={<Button onClick={documents.retry}>重新读取</Button>} />
  return (
    <div
      className={cx(
        'workflow-run-card',
        apiDesignReadinessGate && 'workflow-run-card-api-design-readiness',
        activeConfirmation && 'workflow-run-card-pending',
        revisionImpact && activeConfirmation && 'workflow-run-card-revision-impact'
      )}
    >
      <div className={cx('workflow-run-header')}>
        <div className={cx('workflow-run-title')}>
          <span className={cx('workflow-run-signal')} aria-hidden="true" />
          <Text className={cx('workflow-run-name')} strong>
            工作流执行
          </Text>
        </div>
        {/* 阶段入口卡只表达跳转动作，不显示通用待确认状态标签。 */}
        {!planningStageEntry ? (
          <Tag
            className={cx(
              'workflow-run-status',
              workflowCardStatusVariant && `workflow-run-status-${workflowCardStatusVariant}`
            )}
            color={
              workflowCardResult === 'confirmed'
                ? 'green'
                : workflowCardResult === 'cancelled'
                  ? 'default'
                  : workflowCardResult === 'submitted'
                    ? 'purple'
                    : staleConfirmation
                      ? 'default'
                      : workflowStatusColor(status)
            }
          >
            {workflowCardResult === 'confirmed'
              ? '已确认'
              : workflowCardResult === 'cancelled'
                ? '已取消'
                : workflowCardResult === 'submitted'
                  ? '已提交'
                  : staleConfirmation
                    ? '已失效'
                    : workflowStatusText(status)}
          </Tag>
        ) : null}
      </div>
      {unitTestRunning ? (
        <div className={cx('workflow-run-progress')}>
          <LoadingOutlined aria-hidden="true" />
          <Text type="secondary">正在生成或执行单元测试，请查看执行进度。</Text>
        </div>
      ) : null}
      {workflow.summary.message && !unitTestRunning &&
        !apiDesignReadinessGate &&
        !revisionImpact &&
        !entityDesignReview &&
        !uiDesignConfirmation &&
        !planningStageEntry &&
        !artifactConfirmation &&
        !unitTestConfirmation &&
        !projectLaunch &&
        !dagConfirmation &&
        !testPhaseConfirmation &&
        !reviewPhaseConfirmation &&
        !acceptancePhaseConfirmation &&
        !codeReview && (
          <div className={cx('workflow-run-message')}>
            <Text>{String(workflow.summary.message)}</Text>
          </div>
        )}
      {planningRunning ? (
        <div className={cx('workflow-run-progress')}>
          <LoadingOutlined aria-hidden="true" />
          <Text type="secondary">
            {planningPhase === 'product_planning'
              ? '正在整理需求…'
              : planningPhase === 'technical_planning'
                ? '正在生成技术规划…'
                : '正在生成项目计划…'}
          </Text>
        </div>
      ) : null}
      {codeReview && (
        <CodeReviewCard
          result={codeReviewResult}
          repair={codeReviewRepair}
          scan={codeReviewScan}
          running={workflow.summary.phase === 'code_review' && status === 'running'}
          canRepair={
            codeReviewRepairConfirmation &&
            !disabled &&
            !historicalClarificationAnswers &&
            interactionAvailability === 'active'
          }
          onRepairAll={() =>
            onSubmitClarification?.(workflow, {
              code_review_repair_confirmation: { action: 'repair_all' }
            })
          }
        />
      )}
      {projectLaunch && <ProjectLaunchCard workflow={workflow} />}
      {applicationConfigPrerequisite && application ? (
        <ApplicationConfigPrerequisiteCard
          application={application}
          disabled={disabled || interactionAvailability !== 'active'}
          historical={interactionAvailability !== 'active'}
          result={workflowCardResult}
          pending={(clarification as Record<string, unknown>).configPrerequisite as Record<string, unknown>}
          onAction={onSubmitClarification ? async (action) => {
            await onSubmitClarification(workflow, { application_config_prerequisite: action })
          } : undefined}
        />
      ) : null}
      {/* API 映射门禁使用专用卡片，即使没有通用问题列表也必须提供配置和确认入口。 */}
      {clarification?.mode === 'technical_plan_confirmation' && workflowCardResult === 'confirmed' ? (
        <div className={cx('workflow-clarification')}>
          <PlanConfirmationCard
            confirmed
            disabled={Boolean(reviseConfirmedTechnicalPlanDisabled)}
            onAbandon={() => undefined}
            onConfirm={() => undefined}
            onRevise={(request) => { void onReviseConfirmedTechnicalPlan?.(request) }}
            planType="technical"
            requiresConfirmation={false}
            title="技术规划"
          />
        </div>
      ) : (clarificationQuestions.length > 0 ||
        artifactConfirmation ||
        uiDesignConfirmation ||
        apiDesignRequired ||
        unitTestConfirmation ||
        detailReview ||
        planningStageEntry ||
        dagConfirmation ||
        testPhaseConfirmation ||
        reviewPhaseConfirmation ||
        acceptancePhaseConfirmation) && (
        <div className={cx('workflow-clarification', apiDesignReadinessGate && 'workflow-clarification-api-design-readiness')}>
          {!artifactConfirmation && !reviewStage && !apiDesignReadinessGate && !revisionImpact && !entityDesignReview && !entityDesignGate && !planningStageEntry && (
            <div className={cx('workflow-clarification-header')}>
              <div>
                <Text strong>{staleConfirmation ? '原确认内容' : '待确认事项'}</Text>
              </div>
              <Tag
                className={cx('workflow-confirmation-count')}
                color={activeConfirmation ? 'gold' : 'default'}
              >
                {confirmationItemCount}
              </Tag>
            </div>
          )}
          {requiresConfirmation && interactionAvailability !== 'active' && (
            <Alert
              className={cx('workflow-confirmation-status')}
              message={
                interactionAvailability === 'unavailable'
                  ? '正在校准确认状态，请稍候。'
                  : '此确认已失效，请使用当前计划操作继续。'
              }
              showIcon
              type="info"
            />
          )}
          {revisionImpact && (requiresConfirmation || hasRevisionImpactDecision) ? (
            <RevisionImpactReview
              disabled={disabled || interactionAvailability !== 'active'}
              decision={revisionImpactDecision}
              impact={revisionImpact}
              onDecision={(decision) =>
                onSubmitClarification?.(workflow, {
                  revision_impact_confirmation: decision
                })
              }
            />
          ) : revisionDraftBinding && requiresConfirmation ? (
            <RevisionDraftReview
              disabled={disabled || interactionAvailability !== 'active'}
              technicalPlan={technicalPlanObject || {}}
              baseline={technicalPlanBaseline}
              productPlan={productPlanObject || {}}
              interaction={revisionDraftBinding.interaction}
              key={revisionDraftBinding.draft.draftSha256}
              onAction={(interaction) =>
                onSubmitClarification?.(workflow, {
                  revision_draft_interaction: interaction
                })
              }
            />
          ) : planningStageEntry ? (
            <PlanningStageEntryCard
              disabled={
                disabled || interactionAvailability !== 'active' || !requiresConfirmation
              }
              onEnterPlanning={() =>
                onSubmitClarification?.(workflow, {
                  planning_stage_entry: 'enter',
                  __applicationPlanningAction: 'enter_planning'
                })
              }
            />
          ) : testPhaseConfirmation && requiresConfirmation ? (
            <TestPhaseConfirmationCard
              disabled={disabled || interactionAvailability !== 'active'}
              message={clarification?.message}
              onSubmit={() =>
                onSubmitClarification?.(workflow, {
                  test_phase_confirmation: {
                    action: 'confirm'
                  }
                })
              }
              target={testTarget}
              workspaceRoot={workspaceRoot}
            />
          ) : reviewPhaseConfirmation && requiresConfirmation ? (
            <ReviewPhaseConfirmationCard
              disabled={disabled || interactionAvailability !== 'active'}
              diffFileCount={Number(clarification?.diffReviewFileCount || 0)}
              onSubmit={(reviewMode) =>
                onSubmitClarification?.(workflow, {
                  review_phase_confirmation: {
                    action: 'confirm',
                    reviewMode
                  }
                })
              }
              workspaceRoot={workspaceRoot}
            />
          ) : acceptancePhaseConfirmation && requiresConfirmation ? (
            <AcceptancePhaseConfirmationCard
              disabled={disabled || interactionAvailability !== 'active'}
              onSubmit={() =>
                onSubmitClarification?.(workflow, {
                  acceptance_phase_confirmation: {
                    action: 'confirm'
                  }
                })
              }
              workspaceRoot={workspaceRoot}
            />
          ) : dagConfirmation && requiresConfirmation ? (
            <BuildTaskPlanConfirmation
              disabled={disabled || interactionAvailability !== 'active' || !dagDraftIdentity}
              errors={dagConfirmationErrors}
              onSubmit={(action: WorkflowBuildTaskPlanConfirmation) => {
                const identityBoundAction = bindDagConfirmationDraftIdentity(workflow, action)
                if (!identityBoundAction) return
                onSubmitClarification?.(workflow, {
                  build_task_plan_confirmation: identityBoundAction
                })
              }}
              plan={dagTaskPlan}
              targetReview={clarification?.targetReview}
            />
          ) : unitTestConfirmation && requiresConfirmation ? (
            <UnitTestConfirmationPanel
              disabled={disabled || interactionAvailability !== 'active'}
              message={clarification?.message}
              onSubmit={(decision) =>
                onSubmitClarification?.(workflow, {
                  unit_test_confirmation: decision
                })
              }
            />
          ) : frontendPerformanceConfirmation && requiresConfirmation ? (
            <FrontendPerformanceConfirmationPanel
              disabled={disabled || interactionAvailability !== 'active'}
              message={clarification?.message}
              onSubmit={(decision) =>
                onSubmitClarification?.(workflow, {
                  frontend_performance_confirmation: decision
                })
              }
            />
          ) : apiDesignRequired ? (
            <ApiDesignReadinessGateCard
              disabled={disabled || interactionAvailability !== 'active'}
              designs={clarification?.apiDesigns || []}
              message={clarification?.message}
              savedMappingKeys={apiDesignSavedMappingKeys}
              scopeKey={`${workflow.threadId}:${workflow.runId}`}
              onConfigure={(target) => onOpenApiDesignConfig?.(target, workflow)}
              onConfirm={(action) => onSubmitClarification?.(workflow, { api_design_gate: action })}
              target={normalizeApiDesignDevelopmentTarget(clarification?.developmentTarget)}
            />
          ) : detailReview ? (
            <DetailReview
              disabled={disabled}
              message={clarification?.message}
              onDesignAction={(action) =>
                onSubmitClarification?.(workflow, { entity_design: action })
              }
              onConfirm={(submission) =>
                onSubmitClarification?.(workflow, { entity_source_binding: submission })
              }
              review={detailReview}
              workspaceRoot={workspaceRoot}
            />
          ) : databaseApproval ? (
            <DatabaseApprovalDecision
              approval={databaseApproval.approval}
              disabled={disabled}
              onSubmit={(answer) =>
                onSubmitClarification?.(workflow, {
                  [databaseApprovalAnswerKey]: answer
                })
              }
              statements={databaseApproval.statements}
            />
          ) : entityDesignGate ? (
            <EntityDesignGateCard
              disabled={disabled}
              entities={entityGateEntities}
              explanation={clarification?.message || String(gateQuestion?.question || '')}
              onJump={(entityId) => onEntityDesignGateJump?.(entityId, workflow)}
            />
          ) : artifactConfirmation && (requiresConfirmation || clarification?.mode === 'technical_plan_confirmation') ? (
            clarification?.mode === 'requirement_document_confirmation' ? (
              <RequirementSpecConfirmationCard
                artifact={confirmationArtifact}
                disabled={Boolean(disabled)}
                onSaveRequirementSpec={onSaveRequirementSpec}
                onSubmit={(feedback) =>
                  onSubmitClarification?.(workflow, {
                    requirement_document_confirmation: feedback || '正确，继续规划'
                  })
                }
                requiresConfirmation={requiresConfirmation}
                rootPath={rootPath}
                workflow={workflow}
              />
            ) : (
              <PlanConfirmationCard
                artifact={confirmationArtifact}
                changeSummary={clarification?.mode === 'technical_plan_confirmation' ? technicalChangeSummary : undefined}
                onViewChanges={!disabled && interactionAvailability === 'active' && technicalChanges ? onOpenTechnicalPlanChanges : undefined}
                disabled={Boolean(disabled || interactionAvailability !== 'active')}
                onAbandon={() =>
                  onSubmitClarification?.(workflow, { [artifactAnswerKey]: '需要修改，请重新生成' })
                }
                onConfirm={() =>
                  onSubmitClarification?.(workflow, { [artifactAnswerKey]: '正确，继续' })
                }
                onRevise={(request) =>
                  onSubmitClarification?.(workflow, { technical_plan_confirmation: request })
                }
                plan={
                  clarification?.mode === 'requirement_document_confirmation'
                    ? productPlanObject
                    : clarification?.mode === 'technical_plan_confirmation'
                      ? technicalPlanObject
                      : projectPlanObject
                }
                // 技术规划弹框「页面概览」版块复用上游产物：交互/布局来自 productPlan，风格来自 spec。
                productPlan={productPlanObject}
                spec={requirementSpecObject}
                planType={
                  clarification?.mode === 'requirement_document_confirmation'
                    ? 'product'
                    : clarification?.mode === 'technical_plan_confirmation'
                      ? 'technical'
                      : 'project'
                }
                requiresConfirmation={requiresConfirmation}
                title={artifactConfirmation?.title || '项目计划'}
              />
            )
          ) : uiDesignConfirmation ? (
            <UiDesignConfirmationPanel
              disabled={disabled || interactionAvailability !== 'active'}
              onSubmit={(currentWorkflow, answers) => {
                // 历史 UI 卡保留预览能力，但不能提交生成、调整或正式确认动作。
                if (disabled || interactionAvailability !== 'active') return
                return onSubmitClarification?.(currentWorkflow, answers)
              }}
              onTransferToProductAgent={
                disabled || interactionAvailability !== 'active'
                  ? undefined
                  : onTransferToProductAgent
              }
              showPreview={false}
              iterationOrigins={uiDesignIterationOrigins}
              activePageId={uiDesignActivePageId}
              onActivePageChange={onUiDesignActivePageChange}
              actingPageIds={uiDesignActingPageIds}
              onActingPageIdsChange={onUiDesignActingPageIdsChange}
              workflow={effectiveUiDesignWorkflow}
              workspaceRoot={workspaceRoot}
            />
          ) : (
            <>
              {confirmationArtifact && <ConfirmationArtifact artifact={confirmationArtifact} />}
              <ClarificationContext clarification={clarification} />
              {clarification?.mode === 'implementation_fix_confirmation' && (
                <DirectModificationScopeReview clarification={clarification} />
              )}
              {clarificationQuestions.map((question, index) => {
                // 已答历史卡：保留原控件形态（勾选框/单选/文本框），回填留痕答案并禁用，
                // 让用户看到的回填样式与当初作答时一致，而不是一行只读「已答：」文字。
                const historicalText = historicalClarificationAnswers
                  ? historicalClarificationAnswerFor(
                      historicalClarificationAnswers,
                      question,
                      index
                    )
                  : undefined
                const historicalValue = historicalClarificationAnswers
                  ? historicalClarificationAnswerValue(question, historicalText)
                  : undefined
                return (
                  <div className={cx('workflow-clarification-question')} key={question.id || index}>
                    <div className={cx('workflow-clarification-title')}>
                      <Tag>{question.header || question.dimension || '需求'}</Tag>
                      <Text>{question.question || '请补充需求细节。'}</Text>
                    </div>
                    {historicalClarificationAnswers ? (
                      historicalValue !== undefined ? (
                        <ClarificationQuestionControl
                          disabled
                          onChange={() => undefined}
                          question={question}
                          value={historicalValue}
                        />
                      ) : (
                        <div className={cx('workflow-clarification-history-answer')}>
                          <Text type="secondary">已答：</Text>
                          <Text>{historicalText || '（本轮未作答）'}</Text>
                        </div>
                      )
                    ) : (
                      <ClarificationQuestionControl
                        disabled={Boolean(disabled || !activeConfirmation)}
                        onChange={(value) =>
                          updateAnswer(clarificationQuestionKey(question, index), value)
                        }
                        question={question}
                        value={answers[clarificationQuestionKey(question, index)]}
                      />
                    )}
                    {question.default_assumption && (
                      <Text type="secondary">{question.default_assumption}</Text>
                    )}
                  </div>
                )
              })}
              {requiresConfirmation && clarificationQuestions.length > 0 &&
                !historicalClarificationAnswers && (
                  <Button
                    className={cx('clarification-confirm-btn')}
                    disabled={disabled || !activeConfirmation || !canSubmitClarification}
                    onClick={() => {
                      clearClarificationDraft(workflow.threadId, clarificationQuestions)
                      onSubmitClarification?.(workflow, answers)
                    }}
                    size="large"
                    type="primary"
                  >
                    {implementationFixCancelled ? '确认取消' : '确认并继续'}
                  </Button>
                )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

/** 仅在当前影响确认门读取 revision impact，避免历史影响范围遮蔽后续映射/DAG 确认。 */
function workflowRevisionImpact(
  workflow: WorkflowRunPayload,
  clarification?: WorkflowClarification
): WorkflowRevisionImpact | undefined {
  if (clarification?.mode !== 'revision_impact_confirmation') return undefined
  const clarificationImpact = (clarification as Record<string, unknown> | undefined)?.revisionImpact
  const candidates = [
    clarificationImpact,
    workflow.summary.revisionImpact,
    workflow.state?.revision_impact,
    workflow.state?.revision_impact
  ]
  for (const candidate of candidates) {
    if (!candidate || typeof candidate !== 'object') continue
    const impact = candidate as Partial<WorkflowRevisionImpact>
    if (
      typeof impact.interactionId === 'string' &&
      ['design_stage_revision', 'workbench_plan_revision'].includes(String(impact.formalBranch))
    ) {
      return impact as WorkflowRevisionImpact
    }
  }
  return undefined
}

/** 组合公开草稿、active change 与当前 lifecycle pending interaction，历史卡片不会得到绑定。 */
function workflowRevisionDraftBinding(
  workflow: WorkflowRunPayload,
  clarification?: WorkflowClarification
):
  | {
      draft: WorkflowRevisionDraft
      interaction: Omit<WorkflowRevisionDraftInteraction, 'action' | 'feedback'>
    }
  | undefined {
  if (clarification?.mode !== 'revision_draft_confirmation') return undefined
  const clarificationDraft = (clarification as Record<string, unknown>).revisionDraft
  const candidates = [
    clarificationDraft,
    workflow.summary.revisionDraft,
    workflow.state?.revision_draft,
    workflow.state?.revision_draft
  ]
  const draft = candidates.find(
    (candidate) =>
      candidate &&
      typeof candidate === 'object' &&
      typeof (candidate as Partial<WorkflowRevisionDraft>).artifactKey === 'string' &&
      typeof (candidate as Partial<WorkflowRevisionDraft>).draftSha256 === 'string'
  ) as WorkflowRevisionDraft | undefined
  const lifecycle = workflow.summary.lifecycle
  const active = lifecycle?.activeFormalRevision
  if (!draft || !lifecycle || !active?.changeId || active.currentArtifact !== draft.artifactKey) {
    return undefined
  }
  const pending = Object.values(lifecycle.activeExecutions || {})
    .map((execution) => execution.pendingInteraction)
    .find(
      (interaction) =>
        interaction?.type === 'revision_draft_confirmation' && !interaction.submittedAt
    )
  if (!pending || pending.basedOnRevision !== lifecycle.revision) return undefined
  return {
    draft,
    interaction: {
      changeId: active.changeId,
      interactionId: pending.id,
      basedOnLifecycleRevision: pending.basedOnRevision,
      artifactKey: draft.artifactKey,
      draftSha256: draft.draftSha256
    }
  }
}

/** 从 summary/state/result 读取审查结果，保证审查节点推进到启动节点后卡片仍可渲染。 */
function readCodeReviewResult(workflow: WorkflowRunPayload): WorkflowCodeReviewResult | undefined {
  const candidates: unknown[] = [
    workflow.summary.codeReviewResult,
    workflow.state?.codeReviewResult,
    workflow.state?.code_review_result,
    workflow.state?.codeReviewResult,
    workflow.state?.code_review_result
  ]
  const candidate = candidates.find(
    (value) =>
      value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).length > 0
  )
  return candidate as WorkflowCodeReviewResult | undefined
}

/** 从本次运行的公开快照读取瞬态审查文件，完成态不会回退到旧进度。 */
function readCodeReviewScan(workflow: WorkflowRunPayload): WorkflowCodeReviewScan | undefined {
  const candidates: unknown[] = [
    workflow.summary.codeReviewScan,
    workflow.state?.codeReviewScan,
    workflow.state?.codeReviewScan
  ]
  return candidates.find(
    (value): value is WorkflowCodeReviewScan =>
      Boolean(value) &&
      typeof value === 'object' &&
      !Array.isArray(value) &&
      (value as WorkflowCodeReviewScan).status === 'running' &&
      Boolean(String((value as WorkflowCodeReviewScan).currentFile || '').trim())
  )
}

/** 从 summary/state/result 读取代码审查修复状态。 */
function readCodeReviewRepair(workflow: WorkflowRunPayload): WorkflowCodeReviewRepair | undefined {
  // 同一运行的 summary、state、result 可能在不同帧到达；优先展示状态更靠后的快照，
  // 避免启动节点的旧 awaiting_user 摘要覆盖已经完成的构建检查。
  const repairStatusRank: Record<string, number> = {
    not_required: 1,
    awaiting_user: 2,
    repairing: 3,
    building: 4,
    failed: 5,
    completed: 6
  }
  const candidates: unknown[] = [
    workflow.state?.codeReviewRepair,
    workflow.state?.code_review_repair_result,
    workflow.state?.codeReviewRepair,
    workflow.state?.code_review_repair_result,
    workflow.summary.codeReviewRepair
  ]
  const validCandidates = candidates.filter(
    (value): value is WorkflowCodeReviewRepair =>
      Boolean(value) &&
      value !== null &&
      typeof value === 'object' &&
      !Array.isArray(value) &&
      Object.keys(value).length > 0
  )
  return validCandidates.sort(
    (left, right) =>
      (repairStatusRank[String(right.status || '')] || 0) -
      (repairStatusRank[String(left.status || '')] || 0)
  )[0]
}

/** 从 Workflow 公开状态中读取 RequirementSpec 结构化数据。 */
function readRequirementSpec(workflow: WorkflowRunPayload): Record<string, unknown> | undefined {
  for (const source of [workflow.state]) {
    const value = source?.requirement_spec
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      return value as Record<string, unknown>
    }
  }
  return undefined
}

/**
 * 需求文档确认卡片：展示右侧草稿状态 + 修改（行内输入意见）+ 确认并继续规划。
 * 与计划阶段保持一致：点「修改」不弹结构化编辑框，而是切到行内输入，
 * 用户自由描述修改意见后提交，后端按修订请求重新生成需求文档。
 * 这样不限制用户只能改弹框里列出的模块，任何范围都能用自然语言表达。
 */
function RequirementSpecConfirmationCard({
  disabled,
  onSubmit,
  requiresConfirmation,
  workflow
}: {
  artifact?: WorkflowConfirmationArtifact
  disabled: boolean
  onSaveRequirementSpec?: (
    workflow: WorkflowRunPayload,
    spec: Record<string, unknown>
  ) => Promise<Record<string, unknown> | undefined>
  onSubmit: (feedback?: string) => void
  requiresConfirmation: boolean
  rootPath?: string
  workflow: WorkflowRunPayload
}): ReactElement {
  // 当前审阅门要求重新确认时，旧版本的确认标记不能遮住本轮提示或冒充已确认。
  const requirementsConfirmed = !requiresConfirmation && readRequirementsConfirmed(workflow)
  const clarification = workflowClarification(workflow)
  const errors = clarification?.errors?.map(String).filter(Boolean) || []
  const [revising, setRevising] = useState(false)
  const [revisionRequest, setRevisionRequest] = useState('')

  /** 切换为行内修改输入，并清空上一轮未提交内容。 */
  const startRevision = (): void => {
    setRevisionRequest('')
    setRevising(true)
  }

  /** 仅把非空修改意见提交给既有确认协议，后端识别为修订请求并重新生成。 */
  const submitRevision = (): void => {
    const request = revisionRequest.trim()
    if (!request) return
    setRevising(false)
    onSubmit(request)
  }

  /** 取消行内修改并丢弃本地未提交内容，不触发任何工作流动作。 */
  const cancelRevision = (): void => {
    setRevising(false)
    setRevisionRequest('')
  }

  return (
    <div className={cx('requirement-spec-confirmation-card')}>
      {errors.length > 0 ? (
        <Alert
          description={errors.join('；')}
          message={<Text strong>需求文档需要调整</Text>}
          showIcon
          type="warning"
        />
      ) : null}
      {!requirementsConfirmed ? (
        <div className={cx('requirement-spec-draft-summary')}>
          <div>
            <Text strong>需求文档已生成，已展示在右侧</Text>
          </div>
          <div>
            <Text type="secondary">请在右侧查看内容，确认无误后继续规划，也可以先修改。</Text>
          </div>
        </div>
      ) : null}
      <div
        className={cx(
          'artifact-auth-bar-footer',
          revising && 'technical-plan-revision-inline'
        )}
      >
        {revising ? (
          <div className={cx('technical-plan-revision-editor')}>
            <Input
              aria-label="需求文档修改意见"
              autoFocus
              disabled={disabled}
              onChange={(event) => setRevisionRequest(event.target.value)}
              onKeyDown={(event) => {
                // Escape 只退出本地编辑态，不触发后端修订。
                if (event.key === 'Escape') cancelRevision()
              }}
              placeholder="输入需求文档修改意见"
              value={revisionRequest}
            />
            <Button
              disabled={disabled || !revisionRequest.trim()}
              onClick={submitRevision}
              type="primary"
            >
              提交
            </Button>
            <Button onClick={cancelRevision}>取消</Button>
          </div>
        ) : (
          <>
            <span className={cx('artifact-auth-status')}>
              <CheckCircleOutlined aria-hidden="true" />
              {requirementsConfirmed ? '需求文档已确认' : '需求草稿待确认'}
            </span>
            <span className={cx('artifact-auth-actions')}>
              <Button
                className={cx('requirement-spec-edit-btn')}
                disabled={disabled}
                onClick={startRevision}
              >
                修改
              </Button>
              <Button
                disabled={disabled || !requiresConfirmation}
                onClick={() => onSubmit()}
                type="primary"
              >
                确认并继续规划
              </Button>
            </span>
          </>
        )}
      </div>
    </div>
  )
}

// 从公开 Workflow 快照读取需求确认门禁，禁止把结构化草稿误称为正式文档。
function readRequirementsConfirmed(workflow: WorkflowRunPayload): boolean {
  // 修订首帧的 false 必须覆盖旧快照里的 true，禁止过期文档进入确认卡或编辑入口。
  for (const source of [workflow.state, workflow.state, workflow.summary]) {
    const value = source?.requirementsConfirmed ?? source?.requirements_confirmed
    if (typeof value === 'boolean') return value
  }
  return false
}


function DatabaseApprovalDecision({
  approval,
  disabled,
  onSubmit,
  statements
}: {
  approval: ToolApproval
  disabled?: boolean
  onSubmit: (answer: string) => void
  statements: string[]
}): ReactElement {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // 先落审批记录再恢复工作流：同意时携带审批授权，拒绝时标记拒绝并回传答案。
  const decide = async (action: 'reject' | 'once' | 'operation'): Promise<void> => {
    if (disabled || loading) return
    setLoading(true)
    setError('')
    try {
      if (action === 'reject') {
        await rejectToolRequest(approval.id)
        onSubmit('拒绝执行')
        return
      }
      await approveToolRequest(approval.id, action === 'operation' ? 'operation' : 'once')
      onSubmit(action === 'operation' ? '同意执行，后续相同操作不再询问' : '同意执行，仅本次')
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '数据库审批操作失败。')
    } finally {
      setLoading(false)
    }
  }

  return (
    <>
      <AgentApprovalCard
        allowFeedback={false}
        approval={approval}
        loading={loading}
        onApproveAlways={() => void decide('operation')}
        onApproveOnce={() => void decide('once')}
        onFeedback={() => undefined}
        onReject={() => void decide('reject')}
        statements={statements}
      />
      {error && (
        <Text className={cx('workflow-database-approval-error')} type="danger">
          {error}
        </Text>
      )}
    </>
  )
}

function BuildExecutionSliceProgress({
  executionSlice
}: {
  executionSlice: WorkflowBuildExecutionSlice
}): ReactElement | null {
  /** 展示当前页面或数据源范围的构建进度，不做应用级汇总。 */

  const scope = executionSlice.scope
  if (!scope) return null
  const tasks = Array.isArray(executionSlice.tasks) ? executionSlice.tasks : []
  const summary = executionSlice.summary || {}
  const total = numberValue(summary.total, tasks.length)
  const completed = numberValue(
    summary.completed,
    tasks.filter((task) => buildTaskDisplayStatus(task.status) === 'completed').length
  )
  const failed = numberValue(
    summary.failed,
    tasks.filter((task) => task.status === 'failed').length
  )
  const running = numberValue(
    summary.running,
    tasks.filter((task) => task.status === 'running').length
  )
  const pending = numberValue(
    summary.pending,
    tasks.filter((task) => buildTaskDisplayStatus(task.status) === 'pending').length
  )
  const reused = numberValue(summary.reused, executionSlice.reusable_task_ids?.length || 0)
  const displayTasks = sortBuildTasksForDisplay(tasks)

  return (
    <div className={cx('workflow-build-progress')}>
      <div className={cx('workflow-build-progress-header')}>
        <div>
          <Text strong>执行进度</Text>
        </div>
        <Tag
          className={cx(
            'workflow-build-progress-count-tag',
            failed > 0 ? 'failed' : completed === total && total > 0 ? 'completed' : 'running'
          )}
          color={failed > 0 ? 'red' : completed === total && total > 0 ? 'green' : 'purple'}
        >
          {completed}/{total}
        </Tag>
      </div>
      <div className={cx('workflow-build-progress-stats')}>
        <BuildProgressStat
          icon={<PauseCircleOutlined />}
          label="待执行"
          tone="pending"
          value={pending}
        />
        <BuildProgressStat
          icon={<LoadingOutlined />}
          label="执行中"
          tone="running"
          value={running}
        />
        <BuildProgressStat
          icon={<CheckCircleOutlined />}
          label="已完成"
          tone="completed"
          value={completed}
        />
        <BuildProgressStat
          icon={<CloseCircleOutlined />}
          label="失败"
          tone="failed"
          value={failed}
        />
        <BuildProgressStat
          icon={<ClockCircleOutlined />}
          label="已复用"
          tone="reused"
          value={reused}
        />
      </div>
      {tasks.length > 0 && (
        <div className={cx('workflow-build-task-section')}>
          <Text strong>任务详情</Text>
          <BuildExecutionTaskList tasks={displayTasks} reusableTaskIds={executionSlice.reusable_task_ids} />
        </div>
      )}
      {/* 失败处理与任务列表放在同一内容区，保持重试入口紧随任务。 */}
      {tasks.length > 0 && (
        <BuildTaskRetryNotice
          tasks={tasks}
          waiting={executionSlice.summary?.status === 'awaiting_task_retry'}
        />
      )}
    </div>
  )
}

export function BuildExecutionRunCard({
  executionSlice,
  status
}: {
  executionSlice: WorkflowBuildExecutionSlice
  status: 'running' | 'completed' | 'failed' | 'requires_user_input'
}): ReactElement {
  /** 在对应构建步骤内部渲染独立的构建轮次卡片。 */

  return (
    <section className={cx('workflow-run-card', 'workflow-build-run-card', status)}>
      <div className={cx('workflow-run-header')}>
        <div className={cx('workflow-run-title')}>
          <span className={cx('workflow-run-signal')} aria-hidden="true" />
          <Text className={cx('workflow-run-name')} strong>
            构建执行
          </Text>
        </div>
        <Tag className={cx('workflow-run-status')} color={workflowStatusColor(status)}>
          {executionSlice.summary?.status === 'awaiting_task_retry' ? '等待处理' : workflowStatusText(status)}
        </Tag>
      </div>
      <BuildExecutionSliceProgress executionSlice={executionSlice} />
      {/* 中等提示：本轮模块做完而整体未结束时，记一个候选提交点。
          只提示不弹窗，避免打断后续批次。 */}
      {isModuleCheckpointCandidate({ executionStatus: status, tasks: executionSlice.tasks }) ? (
        <Text className={cx('workflow-build-checkpoint-hint')} type="secondary">
          本轮模块改动已完成，可在版本入口创建提交
        </Text>
      ) : null}
    </section>
  )
}

function BuildProgressStat({
  icon,
  label,
  tone,
  value
}: {
  icon: ReactElement
  label: string
  tone: 'pending' | 'running' | 'completed' | 'failed' | 'reused'
  value: number
}): ReactElement {
  /** 渲染当前构建范围内的单项计数，避免上升到应用级统计。 */

  return (
    <span className={cx('workflow-build-progress-stat', tone)}>
      <span className={cx('workflow-build-progress-stat-icon')} aria-hidden="true">
        {icon}
      </span>
      <Text strong>{value}</Text>
      <Text type="secondary">{label}</Text>
    </span>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export { buildToolActivityPlacement } from './buildExecutionPresentation'

function ClarificationContext({
  clarification
}: {
  clarification?: WorkflowClarification
}): ReactElement | null {
  const groups = (clarification?.selection_groups || []).filter(
    (group) => Array.isArray(group.items) && group.items.length > 0
  )
  const context = clarification?.context
  if (groups.length === 0 && !context) return null

  return (
    <div className={cx('workflow-clarification-context')}>
      {groups.map((group, index) => (
        <SelectionGroup group={group} key={`${group.type || group.title}-${index}`} />
      ))}
      {context && <WorkflowContext context={context} />}
    </div>
  )
}

function SelectionGroup({ group }: { group: WorkflowClarificationSelectionGroup }): ReactElement {
  return (
    <div className={cx('workflow-selection-group')}>
      <Text strong>{group.title || group.type || '候选项'}</Text>
      <ul className={cx('workflow-selection-list')}>
        {(group.items || []).map((item) => (
          <li className={cx('workflow-selection-item')} key={item.id || item.label}>
            <Text>{item.label || item.name || item.id}</Text>
            <ul className={cx('workflow-selection-item-meta')}>
              {item.id && (
                <li>
                  <Text type="secondary">id: </Text>
                  <Text code>{item.id}</Text>
                </li>
              )}
              {item.description && (
                <li>
                  <Text type="secondary">{item.description}</Text>
                </li>
              )}
            </ul>
          </li>
        ))}
      </ul>
    </div>
  )
}

function WorkflowContext({ context }: { context: Record<string, unknown> }): ReactElement {
  const page = objectValue(context.page)
  const layout = objectValue(context.layout)
  const interactions = stringList(context.interactions)
  const dataSources = objectList(context.data_sources)
  const permissions = stringList(context.permissions)

  return (
    <div className={cx('workflow-page-context')}>
      {Object.keys(page).length > 0 && (
        <div className={cx('workflow-page-context-row')}>
          <Text strong>{stringValue(page.name) || '页面'}</Text>
          <Text type="secondary">
            {stringValue(page.path)}
            {stringValue(page.goal) ? `：${stringValue(page.goal)}` : ''}
          </Text>
        </div>
      )}
      {stringList(layout.structure).length > 0 && (
        <div className={cx('workflow-page-context-row')}>
          <Text type="secondary">布局</Text>
          <Text>{stringList(layout.structure).join('、')}</Text>
        </div>
      )}
      {interactions.length > 0 && (
        <div className={cx('workflow-page-context-row')}>
          <Text type="secondary">交互</Text>
          <Text>{interactions.join('、')}</Text>
        </div>
      )}
      {dataSources.length > 0 && (
        <div className={cx('workflow-page-context-row')}>
          <Text type="secondary">数据源</Text>
          <Text>
            {dataSources
              .map((source) => stringValue(source.name) || stringValue(source.id))
              .filter(Boolean)
              .join('、')}
          </Text>
        </div>
      )}
      {permissions.length > 0 && (
        <div className={cx('workflow-page-context-row')}>
          <Text type="secondary">权限</Text>
          <Text>{permissions.join('、')}</Text>
        </div>
      )}
    </div>
  )
}

/** 按问题类型展示结构化确认控件，并保留业务自定义选项文案。 */
function ClarificationQuestionControl({
  disabled,
  onChange,
  question,
  value
}: {
  disabled?: boolean
  onChange: (value: WorkflowClarificationAnswer) => void
  question: WorkflowClarificationQuestion
  value?: WorkflowClarificationAnswer
}): ReactElement {
  const options = (question.options || [])
    .filter((option) => option.label)
    .map((option) => ({
      label: option.label || '',
      value: option.value || option.label || '',
      description: option.description || ''
    }))
  const optionsWithOther =
    question.allowOther !== false && !options.some((option) => option.value === OTHER_OPTION_VALUE)
      ? [...options, { label: '其他', value: OTHER_OPTION_VALUE, description: '' }]
      : options
  const selectedValues = selectedAnswerValues(value)
  const otherSelected = selectedValues.includes(OTHER_OPTION_VALUE)
  const otherValue = answerOtherText(value)
  const setSelectedValues = (selected: string[]): void => {
    onChange({ selected, other: otherValue || undefined })
  }
  const setOtherValue = (other: string): void => {
    onChange({ selected: selectedValues, other })
  }

  if (question.type === 'yesno') {
    const yesNoOptions = options.length >= 2
      ? options.slice(0, 2)
      : [
          { label: '是', value: '是', description: '' },
          { label: '否', value: '否', description: '' }
        ]
    return (
      <>
        <Radio.Group
          disabled={disabled}
          onChange={(event) => setSelectedValues([String(event.target.value)])}
          value={selectedValues[0]}
        >
          {yesNoOptions.map((option) => (
            <Radio key={option.value} value={option.value}>{option.label}</Radio>
          ))}
          {question.allowOther !== false && <Radio value={OTHER_OPTION_VALUE}>其他</Radio>}
        </Radio.Group>
        {otherSelected && (
          <OtherInput disabled={disabled} onChange={setOtherValue} value={otherValue} />
        )}
      </>
    )
  }

  if (question.type === 'choice' && optionsWithOther.length > 0) {
    if (question.multiSelect) {
      return (
        <>
          <Checkbox.Group
            disabled={disabled}
            onChange={(checkedValues) => setSelectedValues(checkedValues.map(String))}
            value={selectedValues}
          >
            {optionsWithOther.map((option) => (
              <Checkbox
                key={option.value}
                value={option.value}
                className={cx('workflow-clarification-option')}
              >
                {option.description ? (
                  <Tooltip
                    title={option.description}
                    placement="top"
                    overlayClassName={cx('workflow-clarification-option-tooltip')}
                  >
                    <span>{option.label}</span>
                  </Tooltip>
                ) : (
                  <span>{option.label}</span>
                )}
              </Checkbox>
            ))}
          </Checkbox.Group>
          {otherSelected && (
            <OtherInput disabled={disabled} onChange={setOtherValue} value={otherValue} />
          )}
        </>
      )
    }

    return (
      <>
        <Radio.Group
          disabled={disabled}
          onChange={(event) => setSelectedValues([String(event.target.value)])}
          value={selectedValues[0]}
        >
          {optionsWithOther.map((option) => (
            <Radio
              key={option.value}
              value={option.value}
              className={cx('workflow-clarification-option')}
            >
              {option.description ? (
                <Tooltip
                  title={option.description}
                  placement="top"
                  overlayClassName={cx('workflow-clarification-option-tooltip')}
                >
                  <span>{option.label}</span>
                </Tooltip>
              ) : (
                <span>{option.label}</span>
              )}
            </Radio>
          ))}
        </Radio.Group>
        {otherSelected && (
          <OtherInput disabled={disabled} onChange={setOtherValue} value={otherValue} />
        )}
      </>
    )
  }

  return (
    <TextArea
      autoSize={{ minRows: 2, maxRows: 4 }}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
      placeholder={question.placeholder || '请输入你的补充说明'}
      value={typeof value === 'string' ? value : ''}
    />
  )
}

function OtherInput({
  disabled,
  onChange,
  value
}: {
  disabled?: boolean
  onChange: (value: string) => void
  value: string
}): ReactElement {
  return (
    <TextArea
      autoSize={{ minRows: 2, maxRows: 4 }}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
      placeholder="请补充其他选择或说明"
      value={value}
    />
  )
}

function clarificationQuestionKey(question: WorkflowClarificationQuestion, index: number): string {
  return question.id || question.header || question.question || String(index)
}

/** 历史留痕按 header 记录：依次尝试 id/header/question 作为 key 命中答案。 */
function historicalClarificationAnswerFor(
  answers: Record<string, string>,
  question: WorkflowClarificationQuestion,
  index: number
): string | undefined {
  for (const key of [question.id, question.header, question.question, String(index)]) {
    if (!key) continue
    const value = answers[key]
    if (typeof value === 'string' && value.trim()) return value
  }
  return undefined
}

/**
 * 把留痕文本答案反解为对应控件的值，让历史卡保留勾选框/单选/文本框原形态回填。
 * 选择题序列化格式为「选项A、选项B（其他文本）」；文本题直接是答案原文。
 * 解析不出有效内容时返回 undefined，调用方回退为只读摘要。
 */
function historicalClarificationAnswerValue(
  question: WorkflowClarificationQuestion,
  text: string | undefined
): WorkflowClarificationAnswer | undefined {
  const trimmed = (text || '').trim()
  if (!trimmed) return undefined
  if (question.type === 'choice' || question.type === 'yesno') {
    // 拆出末尾的「（其他文本）」部分。
    let other: string | undefined
    let main = trimmed
    const otherMatch = trimmed.match(/（([^（）]*)）\s*$/)
    if (otherMatch) {
      other = otherMatch[1].trim() || undefined
      main = trimmed.slice(0, otherMatch.index).trim()
    }
    const selected = main
      .split('、')
      .map((item) => item.trim())
      .filter(Boolean)
    if (selected.length === 0 && !other) return undefined
    return { selected, other }
  }
  return trimmed
}

// 澄清表单草稿本地持久化：恢复（从历史"查看计划"回到待确认阶段）时回填用户已填的答案。
// 后端 clarification 只存问题+选项不存答案，刷新/恢复后 useState({}) 会丢，用 localStorage 兜底。
// key = devagentstudio:clarification-draft:{threadId}:{本轮问题指纹}，指纹随问题变化，新一轮自然读不到旧答案。
const CLARIFICATION_DRAFT_PREFIX = 'devagentstudio:clarification-draft'

function clarificationDraftKey(
  threadId: string,
  questions: WorkflowClarificationQuestion[]
): string {
  if (!threadId || questions.length === 0) return ''
  const fingerprint = questions
    .map((question, index) => clarificationQuestionKey(question, index))
    .join('|')
  return `${CLARIFICATION_DRAFT_PREFIX}:${threadId}:${fingerprint}`
}

function loadClarificationDraft(
  threadId: string,
  questions: WorkflowClarificationQuestion[]
): ClarificationAnswers {
  const key = clarificationDraftKey(threadId, questions)
  if (!key) return {}
  try {
    const raw = window.localStorage.getItem(key)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? (parsed as ClarificationAnswers)
      : {}
  } catch {
    return {}
  }
}

function saveClarificationDraft(
  threadId: string,
  questions: WorkflowClarificationQuestion[],
  answers: ClarificationAnswers
): void {
  const key = clarificationDraftKey(threadId, questions)
  if (!key) return
  if (!answers || Object.keys(answers).length === 0) return
  try {
    window.localStorage.setItem(key, JSON.stringify(answers))
  } catch {
    // quota 超限或隐私模式禁用 localStorage 时静默放弃，不影响填表。
  }
}

function clearClarificationDraft(
  threadId: string,
  questions: WorkflowClarificationQuestion[]
): void {
  const key = clarificationDraftKey(threadId, questions)
  if (!key) return
  try {
    window.localStorage.removeItem(key)
  } catch {
    // 静默忽略。
  }
}

function clarificationAnswerComplete(
  question: WorkflowClarificationQuestion,
  value: WorkflowClarificationAnswer | undefined
): boolean {
  if (question.type === 'choice' || question.type === 'yesno') {
    const selected = selectedAnswerValues(value)
    if (selected.length === 0) return false
    return !selected.includes(OTHER_OPTION_VALUE) || Boolean(answerOtherText(value).trim())
  }
  if (Array.isArray(value)) return value.length > 0
  return typeof value === 'string' && value.trim().length > 0
}

function selectedAnswerValues(value: WorkflowClarificationAnswer | undefined): string[] {
  if (typeof value === 'object' && value && !Array.isArray(value) && 'selected' in value) {
    const selected = value.selected
    return Array.isArray(selected) ? selected.map(String) : [String(selected)]
  }
  if (Array.isArray(value)) return value.map(String)
  return typeof value === 'string' && value ? [value] : []
}

function answerOtherText(value: WorkflowClarificationAnswer | undefined): string {
  return typeof value === 'object' && value && !Array.isArray(value) && 'other' in value
    ? String(value.other || '')
    : ''
}


function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
}

function objectList(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> =>
        Boolean(item && typeof item === 'object')
      )
    : []
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String).filter((item) => item.trim()) : []
}

// eslint-disable-next-line react-refresh/only-export-components
export function workflowOriginalRequest(workflow: WorkflowRunPayload): string {
  const summaryRequest = workflow.summary.request
  if (typeof summaryRequest === 'string' && summaryRequest.trim()) {
    return summaryRequest.trim()
  }

  for (const source of [workflow.state]) {
    const requirementSpec = objectValue(source?.requirement_spec)
    const sourceRequest = requirementSpec.source_request
    if (typeof sourceRequest === 'string' && sourceRequest.trim()) return sourceRequest.trim()
  }

  const resultRequest = workflow.state?.request
  if (typeof resultRequest === 'string' && resultRequest.trim()) return resultRequest.trim()

  const stateRequest = workflow.state?.request
  if (typeof stateRequest === 'string' && stateRequest.trim()) return stateRequest.trim()

  const startedEvent = workflow.events.find((event) => event.type === 'workflow.run.started')
  const eventRequest = startedEvent?.data?.request
  return typeof eventRequest === 'string' ? eventRequest.trim() : ''
}

/** 根据当前结构化交互生成恢复 Workflow 所需的用户可见消息。 */
// eslint-disable-next-line react-refresh/only-export-components
export function buildClarificationContinuationMessage(
  workflow: WorkflowRunPayload,
  answers: ClarificationAnswers
): string {
  const clarification = workflowClarification(workflow)
  const acceptanceMessage = pageAcceptanceContinuationMessage(clarification, answers)
  if (acceptanceMessage) return acceptanceMessage
  if (clarification?.mode === 'entity_source_binding' && answers.entity_source_binding) {
    const submission = answers.entity_source_binding
    if (
      typeof submission === 'object' &&
      !Array.isArray(submission) &&
      'review_status' in submission
    ) {
      return '已整体审阅并确认全部页面和数据源设计，请合并本次结构化修改后继续。'
    }
  }
  if (clarification?.mode === 'entity_source_binding' && answers.entity_design) {
    const actionMessage = entityDesignActionContinuationMessage(answers.entity_design)
    if (actionMessage) return actionMessage
  }
  const questions = clarification?.questions || []
  const originalRequest = workflowOriginalRequest(workflow)
  if (!originalRequest || questions.length === 0) return ''

  const answerLines = questions
    .map((question, index) => {
      const key = clarificationQuestionKey(question, index)
      const value = answers[key]
      const answer = clarificationAnswerText(value, question)
      if (!answer || !String(answer).trim()) return ''
      return `- ${
        question.header || question.dimension || `问题${index + 1}`
      }：${question.question || '请补充需求细节。'}\n  回答：${answer}`
    })
    .filter(Boolean)

  if (answerLines.length === 0) return ''

  return answerLines.join('\n')
}

/** 展示选择项的中文标签，协议枚举仍由原始 answers 单独提交。 */
function clarificationAnswerText(
  value: WorkflowClarificationAnswer | undefined,
  question: WorkflowClarificationQuestion
): string {
  /** 只转换题目声明的选项值，保留自由输入和其他补充原文。 */
  const choiceLabel = (selected: string): string =>
    question.type === 'text'
      ? selected
      : question.options?.find((option) => (option.value || option.label) === selected)?.label ||
        selected
  if (typeof value === 'object' && value && !Array.isArray(value) && 'selected' in value) {
    const selected = selectedAnswerValues(value).filter((item) => item !== OTHER_OPTION_VALUE)
    const parts = selected.length > 0 ? [`已选：${selected.map(choiceLabel).join('、')}`] : []
    const other = answerOtherText(value).trim()
    if (other) parts.push(`其他补充：${other}`)
    return parts.join('；')
  }
  if (Array.isArray(value)) return value.map(choiceLabel).join('、')
  return typeof value === 'string' ? choiceLabel(value) : ''
}

// 从 Workflow payload 的多个位置读取待确认载荷，兼容流式快照、最终结果和自定义事件。
// 从 workflow.state/result.project_plan 读取结构化项目计划，供 ProjectPlanSummary 展示。
function readProjectPlan(workflow: WorkflowRunPayload): Record<string, unknown> | undefined {
  for (const source of [workflow.state]) {
    const value = source?.project_plan
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      return value as Record<string, unknown>
    }
  }
  return undefined
}

/** 从 Workflow 公开状态中读取 ProductPlan 结构化数据。 */
function readProductPlan(workflow: WorkflowRunPayload): Record<string, unknown> | undefined {
  for (const source of [workflow.state]) {
    const value = source?.product_plan
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      return value as Record<string, unknown>
    }
  }
  return undefined
}

/** 从 Workflow 公开状态中读取 TechnicalPlan 结构化数据。 */
function readTechnicalPlan(workflow: WorkflowRunPayload): Record<string, unknown> | undefined {
  for (const source of [workflow.state]) {
    const value = source?.technical_plan
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      return value as Record<string, unknown>
    }
  }
  return undefined
}

// 提取高危数据库审批载荷：必须是 agent_approval 模式且包含可执行的 SQL 语句。
function workflowDatabaseApproval(
  clarification?: WorkflowClarification
): { approval: ToolApproval; statements: string[] } | undefined {
  if (!clarification || clarification.mode !== 'agent_approval') return undefined
  const approval = clarification.approval
  if (!approval || typeof approval !== 'object' || Array.isArray(approval)) {
    return undefined
  }
  const plan = clarification.database_change_plan
  const planRecord =
    plan && typeof plan === 'object' && !Array.isArray(plan)
      ? (plan as Record<string, unknown>)
      : {}
  const rawStatements = planRecord.statements
  const statements = Array.isArray(rawStatements) ? rawStatements.map(String).filter(Boolean) : []
  if (statements.length === 0) return undefined
  return { approval: approval as ToolApproval, statements }
}

function workflowConfirmationArtifact(
  workflow: WorkflowRunPayload,
  clarification?: WorkflowClarification
): WorkflowConfirmationArtifact | undefined {
  const expectedArtifactId =
    clarification?.mode === 'requirement_document_confirmation'
      ? 'requirement_document'
      : clarification?.mode === 'technical_plan_confirmation'
        ? 'technical_plan'
        : clarification?.mode === 'project_plan_confirmation'
          ? 'project_plan'
          : undefined
  const artifact = workflow.confirmationArtifact

  if (
    !expectedArtifactId ||
    clarification?.status !== 'requires_user_input' ||
    !artifact ||
    artifact.id !== expectedArtifactId ||
    artifact.format !== 'markdown' ||
    !artifact.content?.trim()
  ) {
    return undefined
  }

  return artifact
}


function numberValue(value: unknown, fallback: number): number {
  /** 将后端计数字段规整为有限数字。 */

  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}


/** 将工作流状态映射为符合工作区语义色的标签颜色。 */
function workflowStatusColor(status: string): string {
  if (status === 'completed' || status === 'passed') return 'green'
  if (status === 'failed' || status === 'error') return 'red'
  if (status === 'requires_user_input') return 'red'
  if (status === 'running') return 'purple'
  return 'default'
}

function workflowStatusText(status: string): string {
  /** 将工作流状态映射为中文标签。 */

  if (status === 'completed' || status === 'passed') return '完成'
  if (status === 'failed' || status === 'error') return '失败'
  if (status === 'requires_user_input') return '待确认'
  if (status === 'running') return '运行中'
  return status || '未知'
}
