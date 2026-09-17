import type { WorkflowEvent, WorkflowRunPayload } from '../../typings'
import { planningGate, type InitializationPlanningRecord } from '../../initializationPlanning'
import { REQUIREMENT_SPEC_GENERATION_PROMPT } from '../../planning/requirementQuality'
import { appDataByWorkspace } from '../../../../../mock-data'
import { WORKSPACE_DOC_PATHS } from '../workspaceFiles'
import { planningWorkflow } from './planningRuntime'

/** 由当前领域阶段生成唯一待交互快照，恢复与正常运行使用同一投影。 */
export function planningSnapshot(
  record: InitializationPlanningRecord,
  threadId: string,
  events: WorkflowEvent[] = []
): WorkflowRunPayload {
  const stage = record.stage
  const stages: Record<string, [string, string, string]> = {
    collecting_requirement: ['requirements', 'requirement_clarification', '请补充应用的业务需求。'],
    analyzing_requirement: ['requirements', 'requirement_clarification', '请核对业务范围与角色。'],
    generating_requirement_document: ['requirement_document', '', '正在生成需求规格说明书。'],
    awaiting_requirement_document_confirmation: [
      'requirement_document',
      'requirement_document_confirmation',
      '请审阅需求规格说明书，确认后生成 UI 设计稿。'
    ],
    generating_ui_designs: ['ui_confirmation', '', '正在生成页面设计稿。'],
    awaiting_ui_design_confirmation: [
      'ui_confirmation',
      'ui_design_confirmation',
      '请逐页审阅设计稿。'
    ],
    awaiting_planning_stage_entry: [
      'planning_stage_entry',
      'planning_stage_entry',
      '产品设计已确认，可以进入计划阶段。'
    ],
    generating_technical_plan: ['technical_planning', '', '正在生成技术规划方案。'],
    awaiting_technical_plan_confirmation: [
      'technical_planning',
      'technical_plan_confirmation',
      '请审阅架构、应用API与应用页面绑定。'
    ],
    generating_application_template_files: ['template_generation', '', '正在准备应用模板。'],
    // 开发准入门：模板生成完成后等用户在统一弹框中选择任务类型确认进入开发。
    awaiting_development_entry: [
      'ready_for_workbench',
      'development_entry_confirmation',
      '应用模板已生成，请确认开发准入。'
    ],
    ready_for_workbench: ['ready_for_workbench', '', '初始化已完成，可以开始开发。']
  }
  const [phase, mode, message] = stages[stage] || stages.collecting_requirement
  const stopped = record.operationStatus === 'failed' || record.operationStatus === 'cancelled'
  const status = stopped
    ? 'requires_user_input'
    : mode
      ? 'requires_user_input'
      : stage === 'ready_for_workbench'
        ? 'completed'
        : 'running'
  const scenario = appDataByWorkspace(record.workspaceRoot)
  const questions =
    mode === 'requirement_clarification'
      ? scenario.clarificationQuestions.map((question) => ({
          ...question,
          question: String(question.question || '').replaceAll(
            scenario.app.name,
            record.applicationName
          )
        }))
      : []
  const clarification = {
    mode: stopped ? 'planning_recovery' : mode,
    status: 'requires_user_input',
    message: stopped ? record.error || '生成已停止，可继续当前节点。' : message,
    questions: stopped ? [] : questions
  }
  const confirmation = {
    confirmedAt: record.updatedAt,
    directories: { specs: 'docs', plans: 'docs', uiDesigns: 'docs' },
    artifacts: {
      requirementSpec: WORKSPACE_DOC_PATHS.requirementSpec,
      productPlan: WORKSPACE_DOC_PATHS.productPlan,
      uiDesigns: WORKSPACE_DOC_PATHS.uiDesigns,
      technicalPlan: WORKSPACE_DOC_PATHS.technicalPlan
    }
  }
  const state = {
    planningGate: planningGate(record),
    planningRevision: record.revision,
    planningVersionId: record.versionId,
    planningApplicationId: record.applicationId,
    clarification: mode || stopped ? clarification : undefined,
    clarificationHistory: record.workflow?.state?.clarificationHistory || [],
    clarificationAnswers: record.clarificationAnswers,
    requirement_spec: record.artifacts.requirementSpec,
    product_plan: record.artifacts.productPlan,
    requirement_spec_generation_prompt: REQUIREMENT_SPEC_GENERATION_PROMPT,
    ui_designs: record.artifacts.uiDesigns,
    technical_plan: record.artifacts.technicalPlan,
    product_design_confirmed: stage === 'awaiting_planning_stage_entry',
    ...(stage === 'ready_for_workbench' ? { application_planning_confirmation: confirmation } : {})
  }
  return planningWorkflow(
    threadId,
    phase,
    status,
    state,
    {
      summary: {
        phase,
        status,
        message,
        clarification: mode || stopped ? clarification : undefined
      },
      result: state
    },
    events
  )
}
