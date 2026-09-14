import { appDataByWorkspace } from '../../../../../mock-data'
import type { ApplicationConfig, WorkflowRunPayload } from '../../typings'
import type { SendWorkflowMessageOptions } from '../../service/agUiAgent'
import {
  ensureInitializationPlanningRecord,
  persistInitializationPlanningRecord,
  planningGate,
  transitionInitializationPlanning,
  type InitializationPlanningEvent,
  type InitializationPlanningRecord,
  type InitializationPlanningSeed,
  type PlanningAction
} from '../../initializationPlanning'
import { applyPlanningMarkdown, refreshPlanningDocuments } from '../../planning/documents'
import { applyRequirementForm } from '../../planning/requirements'
import { assertRequirementReady } from '../../planning/requirementQuality'
import { planningSnapshot } from './planningPayloads'
import {
  createPlanningTrajectory,
  planningDelay,
  planningLifecycle,
  type PlanningReplayCallbacks
} from './planningRuntime'

/** 精确读取当前应用版本，种子只在第一次进入时使用。 */
export function loadPlanningRecord(application: ApplicationConfig): InitializationPlanningRecord {
  const scenario = appDataByWorkspace(application.workspaceRoot)
  return ensureInitializationPlanningRecord(application, {
    requirementSpec: scenario.requirementSpec,
    productPlan: scenario.productPlan,
    uiDesigns: scenario.uiDesigns as InitializationPlanningSeed['uiDesigns'],
    technicalPlan: scenario.technicalPlan
  })
}

/** 保存领域结果，再投影 lifecycle；不允许 UI 或脚本各自猜测阶段。 */
function saveRecord(
  application: ApplicationConfig,
  record: InitializationPlanningRecord,
  callbacks: PlanningReplayCallbacks
): InitializationPlanningRecord {
  refreshPlanningDocuments(record)
  persistInitializationPlanningRecord(record, application)
  callbacks.onApplicationLifecycle?.(planningLifecycle(application, record.stage, record))
  return record
}

/** 执行经过校验的领域事件。 */
function advance(
  application: ApplicationConfig,
  event: InitializationPlanningEvent,
  callbacks: PlanningReplayCallbacks
): InitializationPlanningRecord {
  return saveRecord(
    application,
    transitionInitializationPlanning(loadPlanningRecord(application), event),
    callbacks
  )
}

/** 将当前待办保存为可恢复快照并通过 AG-UI 发射。 */
function project(
  application: ApplicationConfig,
  threadId: string,
  callbacks: PlanningReplayCallbacks,
  events: WorkflowRunPayload['events'] = []
): WorkflowRunPayload {
  const record = loadPlanningRecord(application)
  const payload = planningSnapshot(record, threadId, events)
  if (!events.length && record.stage === 'analyzing_requirement') {
    const trajectory = createPlanningTrajectory('requirement_analysis', callbacks.onProcessSteps)
    trajectory.set('requirements_context', 'completed')
    trajectory.set('requirements_analyze', 'completed')
    trajectory.set('requirements_clarify', 'requires_user_input')
  }
  record.workflow = payload
  saveRecord(application, record, callbacks)
  callbacks.onWorkflow?.(payload)
  return payload
}

/** 只模拟可观察的生成耗时，状态与产物由领域层负责，取消后不再落定节点。 */
async function generate(
  application: ApplicationConfig,
  threadId: string,
  callbacks: PlanningReplayCallbacks
): Promise<WorkflowRunPayload> {
  const record = loadPlanningRecord(application)
  const config: Record<
    string,
    { graph: string; nodes: string[]; done: InitializationPlanningEvent; gate?: string }
  > = {
    generating_requirement_document: {
      graph: 'requirement_analysis',
      nodes: ['requirements_document', 'product_plan'],
      done: { type: 'requirement_document_ready' },
      gate: 'requirement_document_review'
    },
    generating_ui_designs: {
      graph: 'requirement_analysis',
      nodes: ['ui_designs'],
      done: { type: 'ui_designs_ready' },
      gate: 'ui_design_review'
    },
    generating_technical_plan: {
      graph: 'project_planning',
      nodes: ['planning_context', 'planning_scope', 'planning_permissions', 'planning_document'],
      done: { type: 'technical_plan_ready' },
      gate: 'planning_document'
    },
    generating_application_template_files: {
      graph: 'project_planning',
      nodes: ['template_generation'],
      done: { type: 'template_generation_completed' }
    }
  }
  const segment = config[record.stage]
  if (!segment) return project(application, threadId, callbacks)
  record.operationStatus = 'running'
  delete record.error
  saveRecord(application, record, callbacks)
  const trajectory = createPlanningTrajectory(segment.graph, callbacks.onProcessSteps)
  for (const node of segment.nodes) {
    trajectory.set(node, 'running')
    project(application, threadId, callbacks, trajectory.events())
    await planningDelay(420, callbacks.signal)
    trajectory.set(node, 'completed')
  }
  advance(application, segment.done, callbacks)
  if (segment.gate) trajectory.set(segment.gate, 'requires_user_input')
  // 计划阶段以模板生成为最后一环：追加一句 Agent 检查收尾文本（与设计阶段的收尾同型）；
  // 进入开发阶段由统一的开发准入门弹框承载，对话区不渲染门禁卡。
  if (segment.done.type === 'template_generation_completed')
    callbacks.onContent?.('技术规划方案已确认，应用模板已生成，计划阶段完成。')
  return project(application, threadId, callbacks, trajectory.events())
}

/** 保存澄清答案与只读历史，答案仅补齐需求，不自动确认正式文档。 */
function applyAnswers(
  application: ApplicationConfig,
  resume: WorkflowRunPayload,
  answers: Record<string, unknown>,
  callbacks: PlanningReplayCallbacks
): void {
  const record = loadPlanningRecord(application)
  record.clarificationAnswers = answers
  const clarification = resume.state?.clarification as { questions?: Array<Record<string, any>> }
  const constraints = (clarification?.questions || [])
    .map((question) => {
      const value: any = answers[String(question.id)]
      const answer =
        typeof value === 'string'
          ? value
          : Array.isArray(value)
            ? value.join('、')
            : [
                ...(Array.isArray(value?.selected) ? value.selected : [value?.selected]),
                value?.other
              ]
                .filter(Boolean)
                .join('、')
      return answer ? `${question.header || question.question}：${answer}` : ''
    })
    .filter(Boolean)
  record.artifacts.requirementSpec.business_constraints = constraints
  record.artifacts.productPlan.business_constraints = constraints
  record.workflow = {
    ...resume,
    state: {
      ...resume.state,
      clarificationHistory: [
        {
          nodeName: 'requirements_clarify',
          clarification: {
            ...clarification,
            mode: 'requirement_clarification',
            status: 'submitted'
          },
          answers
        }
      ]
    }
  }
  saveRecord(application, record, callbacks)
}

/** 结构化编辑/单页动作统一走同一个规划运行，拒绝越阶段与过期提交。 */
async function performAction(
  application: ApplicationConfig,
  threadId: string,
  action: PlanningAction,
  callbacks: PlanningReplayCallbacks
): Promise<WorkflowRunPayload> {
  const record = loadPlanningRecord(application)
  switch (action.action) {
    case 'save_requirements':
      if (!action.form) throw new Error('缺少需求表单。')
      saveRecord(application, applyRequirementForm(record, action.form), callbacks)
      break
    case 'save_document':
      if (!action.artifactKey || action.markdown === undefined)
        throw new Error('缺少要保存的正式文档。')
      saveRecord(
        application,
        applyPlanningMarkdown(record, action.artifactKey, action.markdown),
        callbacks
      )
      break
    case 'select_template':
      // 批量选模板：一次为全部待确认页面定版式，统一交后台重画、右侧统一呈现。
      if (!action.pages?.length) throw new Error('请先为页面选择版式模板。')
      advance(application, { type: 'revise_ui_designs', pages: action.pages }, callbacks)
      return generate(application, threadId, callbacks)
    case 'revise':
      if (!action.feedback?.trim()) throw new Error('请填写修改意见。')
      advance(
        application,
        {
          type:
            record.stage === 'awaiting_technical_plan_confirmation'
              ? 'revise_technical_plan'
              : 'revise_requirement_document',
          feedback: action.feedback.trim()
        },
        callbacks
      )
      return generate(application, threadId, callbacks)
    case 'confirm':
      if (record.stage === 'awaiting_requirement_document_confirmation') {
        assertRequirementReady(record.artifacts.requirementSpec, record.artifacts.productPlan)
        advance(application, { type: 'confirm_requirement_document' }, callbacks)
      } else if (record.stage === 'awaiting_technical_plan_confirmation')
        advance(application, { type: 'confirm_technical_plan' }, callbacks)
      else throw new Error('当前没有待确认的规划文档。')
      return generate(application, threadId, callbacks)
    case 'retry':
      saveRecord(application, { ...record, operationStatus: 'idle', error: undefined }, callbacks)
      return generate(application, threadId, callbacks)
    default:
      throw new Error('不支持的规划操作。')
  }
  return project(application, threadId, callbacks)
}

/** 回放有持久状态的初始化旅程；新建与历史版本派生均经过同一组明确确认门。 */
export async function replayPlanning(
  threadId: string,
  options: SendWorkflowMessageOptions,
  callbacks: PlanningReplayCallbacks
): Promise<WorkflowRunPayload> {
  const application = options.application || appDataByWorkspace().app
  const version = application.versions?.find((item) => item.id === application.currentVersionId)
  if (version?.status === 'released') throw new Error('已生成版本只读，请先发起迭代。')
  const resume = options.resumeState as WorkflowRunPayload | undefined
  const answers = options.clarificationAnswers as Record<string, unknown> | undefined
  const record = loadPlanningRecord(application)
  if (resume && answers) {
    if (resume.state?.planningGate !== planningGate(record))
      throw new Error('这条确认已过期，请使用当前版本的最新审阅操作。')
    const action = answers.planning_action as PlanningAction | undefined
    if (action) return performAction(application, threadId, action, callbacks)
    const mode = (resume.state?.clarification as { mode?: string })?.mode
    if (mode === 'requirement_clarification') {
      applyAnswers(application, resume, answers, callbacks)
      advance(application, { type: 'start_requirement_document' }, callbacks)
      return generate(application, threadId, callbacks)
    }
    if (mode === 'ui_design_confirmation') {
      const choice = answers.ui_design_action
      if (!['confirm', 'skip'].includes(String(choice))) throw new Error('请选择明确的 UI 操作。')
      advance(
        application,
        { type: choice === 'skip' ? 'skip_ui_designs' : 'confirm_ui_designs' },
        callbacks
      )
      // 阶段完成只输出一句 agent 文本收尾；进入计划阶段由统一的阶段门禁弹框承载
      //（弹框可关闭，稍后从顶部阶段条「计划阶段」再次唤起），对话区不再渲染门禁大卡。
      callbacks.onContent?.(
        choice === 'skip'
          ? '已跳过 UI 设计，设计阶段完成。'
          : '设计阶段的产物已全部确认，设计阶段完成。'
      )
      return project(application, threadId, callbacks)
    }
    if (mode === 'planning_stage_entry' && answers.planning_stage_entry) {
      advance(application, { type: 'enter_planning' }, callbacks)
      return generate(application, threadId, callbacks)
    }
    if (mode === 'development_entry_confirmation' && answers.test_case_task_type) {
      // 开发准入门确认：登记任务类型并推进到 ready_for_workbench（生命周期与阶段条随之
      // 进入开发阶段），随后补一句 Agent 收尾文本，与本轮其它确认的“一来回”形态一致。
      saveRecord(
        application,
        {
          ...record,
          clarificationAnswers: {
            ...record.clarificationAnswers,
            test_case_task_type: answers.test_case_task_type
          }
        },
        callbacks
      )
      advance(application, { type: 'confirm_development_entry' }, callbacks)
      callbacks.onContent?.('开发准入已确认，可以进入开发阶段。')
      return project(application, threadId, callbacks)
    }
    throw new Error('请使用当前节点的确认或修改按钮。')
  }
  if (record.stage === 'collecting_requirement') {
    advance(application, { type: 'start_design' }, callbacks)
    if (version?.parentVersionId)
      callbacks.onContent?.(
        '已继承所选版本的需求与规划内容。请核对本轮范围；新的产物仍需逐步确认。'
      )
    return project(application, threadId, callbacks)
  }
  if (record.operationStatus === 'failed' || record.operationStatus === 'cancelled')
    return project(application, threadId, callbacks)
  if (record.stage.startsWith('generating_')) return generate(application, threadId, callbacks)
  const text = String(options.message || '').trim()
  if (
    text &&
    !['开始产品设计', '开始需求分析', '开始设计', '开始项目规划', '开始技术规划'].includes(text)
  ) {
    callbacks.onContent?.(
      '当前产物已在右侧打开。可直接编辑 Markdown 或使用“提出修改”；确认与进入下一阶段只通过明确按钮执行。'
    )
  }
  return project(application, threadId, callbacks)
}

/** 工作台设计与规划会话复用同一应用版本领域记录。 */
export const replayDesignPhase = replayPlanning

/** 记录停止或失败后的可重试状态，恢复时不伪造节点成功。 */
export function recordPlanningFailure(
  application: ApplicationConfig,
  threadId: string,
  error: string,
  cancelled: boolean,
  callbacks: PlanningReplayCallbacks
): WorkflowRunPayload {
  const record = loadPlanningRecord(application)
  record.operationStatus = cancelled ? 'cancelled' : 'failed'
  record.error = error
  saveRecord(application, record, callbacks)
  return project(application, threadId, callbacks)
}
