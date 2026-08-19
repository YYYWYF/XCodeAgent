import type { WorkflowRunPayload } from '../../typings'

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
    title: '正在生成需求文档',
    revisionTitle: '正在重新生成需求文档',
    detail: '正在分析产品目标、用户角色、页面与业务流程。',
    revisionDetail: '正在合并本次变更，并保留未受影响的需求事实。'
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
  return String(workflow?.summary.phase || '')
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
  // 首次创建的需求、产品和技术阶段使用活动块；UI 首次生成由设计预览区反馈。
  // 只有设计变更显示“重新生成”和意图标签。
  // UI 设计卡片的结构化动作会保留历史意图但显式关闭设计变更，此时沿用卡片自身加载态。
  if (
    !copy ||
    (phase === 'ui_confirmation' && !isRevision) ||
    (!designChangeSubmission && Boolean(intent.target)) ||
    !['running', 'failed'].includes(status)
  ) {
    return undefined
  }
  const title = isRevision ? copy.revisionTitle : copy.title
  return {
    status: status === 'failed' ? 'failed' : 'running',
    title: status === 'failed' ? `${title.replace('正在', '')}失败` : title,
    detail:
      status === 'failed'
        ? String(workflow.summary.message || '当前设计产物生成失败，请重试。')
        : isRevision
          ? intent.reason || copy.revisionDetail || copy.detail
          : copy.detail,
    intentLabel: isRevision ? DESIGN_INTENT_LABELS[intent.target] || intent.target : undefined
  }
}

// 读取设计变更开始前冻结的产物状态，缺少快照时仅使用当前公开产物作兜底。
function hasExistingPlanningArtifact(workflow: WorkflowRunPayload, phase: string): boolean {
  for (const source of [workflow.result, workflow.state]) {
    const presence = readRecord(source?.design_change_existing_artifacts)
    if (typeof presence[phase] === 'boolean') return presence[phase] === true
  }

  const artifactFields: Record<string, string[]> = {
    requirements: ['requirement_spec', 'requirement_spec_path'],
    product_planning: ['product_plan', 'product_plan_path'],
    ui_confirmation: ['ui_designs'],
    technical_planning: ['technical_plan', 'technical_plan_path']
  }
  return artifactFields[phase]?.some((field) =>
    [workflow.result, workflow.state].some((source) => Boolean(source?.[field]))
  ) ?? false
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
