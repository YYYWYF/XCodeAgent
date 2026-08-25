import type {
  WorkflowClarification,
  WorkflowClarificationAnswers,
  WorkflowRunPayload
} from '../../typings'

export type PlanningWorkflowActivity = {
  status: 'running' | 'completed' | 'failed'
  title: string
  detail: string
  intentLabel?: string
}

const PLANNING_ACTIVITY_COPY: Record<
  string,
  { title: string; revisionTitle: string; detail: string; revisionDetail?: string }
> = {
  requirements: {
    title: '正在分析需求',
    revisionTitle: '正在重新分析需求',
    detail: '正在识别产品目标、用户角色、页面与业务流程中的信息缺口。',
    revisionDetail: '正在合并本次补充，并保留未受影响的需求事实。'
  },
  product_planning: {
    title: '正在生成产品规划',
    revisionTitle: '正在重新生成产品规划',
    detail: '正在梳理页面目标、核心操作、状态与产品验收标准。',
    revisionDetail: '正在更新受影响页面的目标、操作、状态与产品验收标准。'
  },
  ui_confirmation: {
    title: '正在生成 UI 设计稿',
    revisionTitle: '正在重新生成 UI 设计稿',
    detail: '正在生成各页面的布局、视觉与交互呈现。',
    revisionDetail: '正在更新受影响页面的布局、视觉与交互呈现。'
  },
  technical_planning: {
    title: '正在生成技术规划',
    revisionTitle: '正在重新生成技术规划',
    detail: '正在根据已确认的上游设计生成技术实现方案。',
    revisionDetail: '正在根据本次设计变更更新技术实现方案。'
  },
  project_planning: {
    title: '正在生成项目计划',
    revisionTitle: '正在重新生成项目计划',
    detail: '正在整理页面、接口与工程实施计划。'
  }
}

const DESIGN_INTENT_LABELS: Record<string, string> = {
  requirements: '需求层变更',
  product_planning: '产品规划层变更',
  ui_confirmation: 'UI 设计层变更',
  chat: '无需修改正式产物'
}

const PLANNING_CONFIRMATION_ANSWER_KEYS: Record<string, string> = {
  requirement_document_confirmation: 'requirement_document_confirmation',
  ui_design_confirmation: 'ui_design_confirmation',
  technical_plan_confirmation: 'technical_plan_confirmation',
  project_plan_confirmation: 'project_plan_confirmation'
}

const PLANNING_CONFIRMATION_DEFAULTS = new Set([
  '正确，继续',
  '正确，继续规划',
  '确认需求文档，继续',
  '确认全部设计稿'
])

// 从 Workflow 快照读取当前设计阶段的交互说明，统一覆盖 summary/state/result 的投影差异。
function planningWorkflowClarification(
  workflow: WorkflowRunPayload
): WorkflowClarification | undefined {
  for (const value of [
    workflow.summary.clarification,
    workflow.state?.clarification,
    workflow.result?.clarification
  ]) {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      return value as WorkflowClarification
    }
  }
  return undefined
}

// 将聊天区提交的规划答案补齐为服务端中断恢复所需的显式动作。
export function ensureApplicationPlanningAction(
  workflow: WorkflowRunPayload,
  answers: WorkflowClarificationAnswers
): WorkflowClarificationAnswers {
  if (answers.__applicationPlanningAction) return answers

  const clarification = planningWorkflowClarification(workflow)
  const mode = String(clarification?.mode || '')
  if (answers.ui_design_action && typeof answers.ui_design_action === 'object') {
    return { ...answers, __applicationPlanningAction: 'ui_action' }
  }
  if (mode === 'technical_plan_generation_error' || typeof answers.planning_recovery === 'string') {
    return { ...answers, __applicationPlanningAction: 'revise' }
  }

  const confirmationKey = PLANNING_CONFIRMATION_ANSWER_KEYS[mode]
  if (confirmationKey) {
    const confirmationValue = answers[confirmationKey]
    const confirmationText =
      typeof confirmationValue === 'string' ? confirmationValue.trim() : ''
    const action =
      confirmationText && !PLANNING_CONFIRMATION_DEFAULTS.has(confirmationText)
        ? 'revise'
        : 'confirm'
    return { ...answers, __applicationPlanningAction: action }
  }

  // WorkflowRunCard 的需求澄清按钮只传用户答案；此处明确标记为 answer，避免在本地构造请求前失败。
  if (mode === 'ask_user_question' || (clarification?.questions?.length || 0) > 0) {
    return { ...answers, __applicationPlanningAction: 'answer' }
  }
  return answers
}

// 判断当前应用规划是否已经进入可展示的用户交互阶段，避免被仍在收尾的传输状态遮挡。
export function planningWorkflowRequiresUserInput(workflow?: WorkflowRunPayload): boolean {
  if (workflow?.summary.status === 'requires_user_input') return true
  for (const value of [
    workflow?.summary.clarification,
    workflow?.result?.clarification,
    workflow?.state?.clarification
  ]) {
    const clarification =
      value && typeof value === 'object' ? (value as Record<string, unknown>) : undefined
    if (clarification?.status === 'requires_user_input') return true
  }
  return false
}

// 判断规划快照是否已经足以结束聊天占位加载态，覆盖待确认与正常终态。
export function planningWorkflowSettlesLoading(workflow?: WorkflowRunPayload): boolean {
  if (planningWorkflowRequiresUserInput(workflow)) return true
  return ['completed', 'failed'].includes(String(workflow?.summary.status || ''))
}

// 判断设计阶段的空助手消息是否必须继续显示加载态，避免 running 快照只渲染 Agent 头像。
export function planningWorkflowNeedsChatLoading(
  workflow: WorkflowRunPayload | undefined,
  designPhasePlanning: boolean,
  loadingPlaceholder: boolean,
  hasWorkflowCard: boolean,
  content: string,
  isLatestAssistantMessage = false
): boolean {
  if (loadingPlaceholder) return true
  if (!designPhasePlanning || hasWorkflowCard) return false
  if (workflow?.summary.status === 'running') return !content.trim()
  // 规划快照尚未到达的窗口期：仅对当前正在推进的最后一条 assistant 消息生效。
  // 该窗口内无 workflow 的消息只可能来自规划流式 token（中间态输出），不能以纯文本裸露；
  // 历史遗留的无 workflow 消息（后面已有后续消息）不受影响，仍正常展示原文。
  if (!workflow && isLatestAssistantMessage) return true
  return false
}

// 判断权威快照是否可以回填聊天区；用户已提交新一轮时禁止复用上一轮待确认内容。
export function shouldBackfillPlanningWorkflow(
  workflow: WorkflowRunPayload | undefined,
  newRoundPending: boolean
): boolean {
  return !newRoundPending && planningWorkflowSettlesLoading(workflow)
}

// 优先读取最新开始执行的节点，兼容摘要仍停留在上一节点的流式快照。
export function planningWorkflowPhase(workflow?: WorkflowRunPayload): string {
  const events = workflow?.events || []
  const lastEvent = events.length ? events[events.length - 1] : undefined
  if (lastEvent?.type === 'workflow.node.started') {
    const startedPhase = lastEvent.nodeName || lastEvent.node?.id
    if (startedPhase) return String(startedPhase)
  }
  return String(workflow?.summary?.phase || '')
}

// 从 Workflow 或生命周期快照读取当前需求是否已通过用户确认门禁。
export function planningRequirementsConfirmed(
  workflow?: WorkflowRunPayload,
  requirementSpecPath?: string
): boolean {
  // UI 与 TechnicalPlan 只能消费已联合确认的需求文档；节点切换的增量帧
  // 可能暂时缺少该字段或把缺失值投影为 false，此时以下游阶段门禁为权威。
  if (
    ['ui_confirmation', 'technical_planning', 'project_planning'].includes(
      planningWorkflowPhase(workflow)
    )
  ) {
    return true
  }
  // 以当前 state/result 中第一个明确布尔值为准；修订首帧的 false 必须覆盖旧快照里的 true。
  for (const source of [workflow?.state, workflow?.result, workflow?.summary]) {
    const value = source?.requirementsConfirmed ?? source?.requirements_confirmed
    if (typeof value === 'boolean') return value
  }
  // 重新打开应用时可能没有内存 Workflow；此时只以本次真实读取到的正式 Markdown 路径兜底。
  return Boolean(
    requirementSpecPath && !requirementSpecPath.match(/[\\/]drafts[\\/]/)
  )
}

// 从公开 Workflow 状态读取应用规划生命周期的当前阶段。
export function planningWorkflowLifecycleStage(workflow?: WorkflowRunPayload): string {
  for (const source of [workflow?.state, workflow?.result, workflow?.summary]) {
    const lifecycle = source?.lifecycle
    if (!lifecycle || typeof lifecycle !== 'object') continue
    const initialization = (lifecycle as Record<string, unknown>).initialization
    if (!initialization || typeof initialization !== 'object') continue
    const stage = (initialization as Record<string, unknown>).stage
    if (typeof stage === 'string' && stage) return stage
  }
  return ''
}

// 只在本轮 TechnicalPlan 已确认并进入模板阶段时允许触发模板初始化，拒绝上游修订快照携带的旧终态确认。
export function planningTechnicalPlanConfirmed(workflow?: WorkflowRunPayload): boolean {
  if (
    planningWorkflowPhase(workflow) !== 'technical_planning' ||
    workflow?.summary.status !== 'completed' ||
    planningWorkflowLifecycleStage(workflow) !== 'generating_application_template_files'
  ) {
    return false
  }
  // 以当前 state 的第一个明确状态为准，避免旧 result 中的 confirmed 覆盖新一轮 pending 状态。
  for (const source of [workflow?.state, workflow?.result]) {
    const technicalPlan = source?.technical_plan
    if (!technicalPlan || typeof technicalPlan !== 'object' || Array.isArray(technicalPlan)) continue
    const confirmationStatus = (technicalPlan as Record<string, unknown>).confirmation_status
    if (typeof confirmationStatus === 'string') return confirmationStatus === 'confirmed'
  }
  return false
}

// 判断当前是否正在生成由需求事实与产品规划共同组成的需求文档。
export function planningRequirementsDocumentGenerating(
  workflow?: WorkflowRunPayload,
  lifecycleStage?: string
): boolean {
  for (const source of [workflow?.state, workflow?.result]) {
    const interaction = source?.application_planning_interaction
    if (
      interaction &&
      typeof interaction === 'object' &&
      (interaction as Record<string, unknown>).action === 'confirm' &&
      (interaction as Record<string, unknown>).artifact === 'requirement_document'
    ) {
      return planningWorkflowPhase(workflow) === 'requirements'
    }
  }
  return (
    planningWorkflowPhase(workflow) === 'product_planning' &&
    (lifecycleStage === 'generating_requirement_document' ||
      planningWorkflowLifecycleStage(workflow) === 'generating_requirement_document')
  )
}

// 把创建规划 Graph 的实时节点和意图结果转换为聊天区可直接展示的进度文案。
export function planningWorkflowActivity(
  workflow?: WorkflowRunPayload
): PlanningWorkflowActivity | undefined {
  if (!workflow) return undefined
  const phase = planningWorkflowPhase(workflow)
  const status = String(workflow.summary.status || 'running')
  const intent = readDesignIntent(workflow)
  const designChangeSubmission =
    workflow.state?.design_change_submission === true ||
    workflow.result?.design_change_submission === true

  if (phase === 'design_intent_analysis') {
    if (status === 'failed') {
      return {
        status: 'failed',
        title: '设计变更意图识别失败',
        detail: String(workflow.summary.message || '未能判断这次变更应回到哪个设计阶段。')
      }
    }
    const targetCopy = PLANNING_ACTIVITY_COPY[intent.target]
    if (targetCopy) {
      const isRevision = hasExistingPlanningArtifact(workflow, intent.target)
      return {
        status: 'running',
        title: isRevision ? targetCopy.revisionTitle : targetCopy.title,
        detail:
          intent.reason ||
          (isRevision ? targetCopy.revisionDetail || targetCopy.detail : targetCopy.detail),
        intentLabel: isRevision ? DESIGN_INTENT_LABELS[intent.target] || intent.target : undefined
      }
    }
    if (intent.target === 'chat') {
      return {
        status: 'completed',
        title: '这次输入无需修改正式设计产物',
        detail: intent.reason || '已按设计对话处理。',
        intentLabel: DESIGN_INTENT_LABELS.chat
      }
    }
    return {
      status: 'running',
      title: '正在识别设计变更意图',
      detail: '正在判断这次改动应回到需求、产品规划还是 UI 设计阶段。'
    }
  }

  if (phase === 'design_chat_response') {
    return {
      status: status === 'failed' ? 'failed' : 'completed',
      title: '这次输入无需修改正式设计产物',
      detail:
        readText(workflow.result?.conversation_response) ||
        readText(workflow.state?.conversation_response) ||
        intent.reason ||
        '已按设计对话处理。',
      intentLabel: DESIGN_INTENT_LABELS.chat
    }
  }

  const copy = PLANNING_ACTIVITY_COPY[phase]
  const isRevision =
    designChangeSubmission &&
    Boolean(intent.target) &&
    hasExistingPlanningArtifact(workflow, phase)
  const requirementsDocumentGenerating = planningRequirementsDocumentGenerating(
    workflow,
    planningWorkflowLifecycleStage(workflow)
  )
  const effectiveCopy =
    copy && phase === 'requirements' && requirementsDocumentGenerating
      ? {
          ...copy,
          title: isRevision ? '正在重新生成需求文档' : '正在生成需求文档',
          revisionTitle: '正在重新生成需求文档',
          detail: '正在把已确认的需求草稿写入正式 Markdown 文档。',
          revisionDetail: '正在把本次确认后的需求修订写入正式 Markdown 文档。'
        }
      : copy
  // 首次创建的需求、产品和技术阶段使用活动块；UI 首次生成由设计预览区反馈。
  // 只有设计变更显示“重新生成”和意图标签。
  // UI 设计卡片的结构化动作会保留历史意图但显式关闭设计变更，此时沿用卡片自身加载态。
  if (
    !effectiveCopy ||
    (phase === 'ui_confirmation' && !isRevision) ||
    (!designChangeSubmission && Boolean(intent.target)) ||
    !['running', 'failed'].includes(status)
  ) {
    return undefined
  }
  const title = isRevision ? effectiveCopy.revisionTitle : effectiveCopy.title
  return {
    status: status === 'failed' ? 'failed' : 'running',
    title: status === 'failed' ? `${title.replace('正在', '')}失败` : title,
    detail:
      status === 'failed'
        ? String(workflow.summary.message || '当前设计产物生成失败，请重试。')
        : isRevision
          ? intent.reason || effectiveCopy.revisionDetail || effectiveCopy.detail
          : effectiveCopy.detail,
    intentLabel: isRevision ? DESIGN_INTENT_LABELS[intent.target] || intent.target : undefined
  }
}

// 只读取服务端创建修订事务时冻结的产物状态；缺少快照一律按首次生成展示。
function hasExistingPlanningArtifact(workflow: WorkflowRunPayload, phase: string): boolean {
  for (const source of [workflow.result, workflow.state]) {
    const presence = readRecord(source?.design_change_existing_artifacts)
    if (typeof presence[phase] === 'boolean') return presence[phase] === true
  }

  return false
}

// 从公开 Workflow 状态或意图节点完成事件中读取设计变更路由结果。
function readDesignIntent(workflow: WorkflowRunPayload): { target: string; reason: string } {
  const stateTarget = readText(
    workflow.result?.design_change_target ?? workflow.state?.design_change_target
  )
  const stateReason = readText(
    workflow.result?.design_change_reason ?? workflow.state?.design_change_reason
  )
  if (stateTarget) return { target: stateTarget, reason: stateReason }

  const event = [...(workflow.events || [])]
    .reverse()
    .find(
      (item) =>
        item.type === 'workflow.node.completed' && item.nodeName === 'design_intent_analysis'
    )
  const eventData = readRecord(event?.data)
  const detail = readRecord(eventData.detail)
  return {
    target: readText(detail.target),
    reason: readText(detail.reason)
  }
}

// 仅接受普通对象，避免直接读取未知 AG-UI 扩展值。
function readRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

// 把未知字段收窄为可展示文本。
function readText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}
