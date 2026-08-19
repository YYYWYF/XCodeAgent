import type { WorkflowRunPayload } from '../typings'
import { isConversationWorkflow } from '../components/AiChatPanel/conversationMode'
import {
  readDagGenerationSnapshot,
  readIntegrationTestChecks,
  readProjectPlanUpdate,
  readWorkspaceInspectionSnapshot
} from './agUiAgent'
import type { DagGenerationSnapshot, ProcessStepRecord } from './agUiAgent'

const WORKFLOW_NODE_LABELS: Record<string, string> = {
  detail_confirmation: '页面细节确认',
  inspect_workspace: '扫描工作区代码',
  scan_workspace_code: '扫描工作区代码',
  inspect_database_context: '数据库上下文检查',
  prepare_build_tasks: '构建任务 DAG 生成',
  build: '代码生成与构建协调',
  integration_test: '集成测试与质量门禁',
  launch_project: '启动本地预览',
  acceptance: '用户验收',
  finalize_project: '完成项目',
  handle_failure: '失败处理'
}

/** 组合实时步骤与 Workflow 历史事件，确保重进会话后仍可展示执行进度。 */
export function processStepsForDisplay(
  steps: ProcessStepRecord[] | undefined,
  workflow: WorkflowRunPayload | undefined
): ProcessStepRecord[] | undefined {
  const recoveredSteps = completedWorkflowProcessSteps(workflow)
  let displaySteps = sortProcessStepsForDisplay(mergeRecoveredWorkflowSteps(steps, recoveredSteps))
  if (!displaySteps?.length) return displaySteps
  const finalChecks = completedIntegrationTestChecks(workflow)
  if (finalChecks?.length) {
    const latestTestStepId = [...displaySteps]
      .reverse()
      .find(
        (step) =>
          step.nodeName === 'integration_test' || step.id.startsWith('workflow:integration_test')
      )?.id
    displaySteps = displaySteps.map((step) =>
      step.id === latestTestStepId
        ? { ...step, checks: mergeIntegrationTestChecks(step.checks, finalChecks) }
        : step
    )
  }

  const workspaceInspection = completedWorkspaceInspection(workflow)
  if (workspaceInspection) {
    const workspaceStepId = [...displaySteps]
      .reverse()
      .find(
        (step) =>
          step.nodeName === 'inspect_workspace' ||
          step.nodeName === 'scan_workspace_code' ||
          step.id.startsWith('workflow:inspect_workspace') ||
          step.id.startsWith('direct:scan_workspace_code')
      )?.id
    displaySteps = displaySteps.map((step) =>
      step.id === workspaceStepId ? { ...step, workspaceInspection } : step
    )
  }

  const planUpdate = completedProjectPlanUpdate(workflow)
  if (!planUpdate) return displaySteps
  const targetStepId = planUpdate.attempt
    ? processStepId('detail_confirmation', planUpdate.attempt)
    : [...displaySteps]
        .reverse()
        .find(
          (step) =>
            step.nodeName === 'detail_confirmation' ||
            step.id.startsWith('workflow:detail_confirmation')
        )?.id
  return displaySteps.map((step) =>
    step.id === targetStepId ? { ...step, projectPlanUpdate: planUpdate.snapshot } : step
  )
}

/** 将已完成 Workflow 的结构化结果回填到持久化步骤，避免旧进度帧遮蔽最终产物。 */
function mergeRecoveredWorkflowSteps(
  steps: ProcessStepRecord[] | undefined,
  recoveredSteps: ProcessStepRecord[] | undefined
): ProcessStepRecord[] | undefined {
  if (!steps?.length) return recoveredSteps
  if (!recoveredSteps?.length) return steps

  const mergedSteps = [...steps]
  for (const recoveredStep of recoveredSteps) {
    const existingIndex = findMatchingWorkflowStepIndex(mergedSteps, recoveredStep)
    if (existingIndex < 0) {
      mergedSteps.push(recoveredStep)
      continue
    }

    const existingStep = mergedSteps[existingIndex]
    const dagGeneration = mergeDagGenerationSnapshot(
      existingStep.dagGeneration,
      recoveredStep.dagGeneration
    )
    if (dagGeneration === existingStep.dagGeneration) continue
    mergedSteps[existingIndex] = { ...existingStep, dagGeneration }
  }
  return mergedSteps
}

/** 按稳定 ID 或节点轮次匹配实时步骤与历史完成步骤。 */
function findMatchingWorkflowStepIndex(
  steps: ProcessStepRecord[],
  recoveredStep: ProcessStepRecord
): number {
  const exactIndex = steps.findIndex((step) => step.id === recoveredStep.id)
  if (exactIndex >= 0) return exactIndex

  const recoveredNodeName = workflowStepNodeName(recoveredStep)
  if (!recoveredNodeName) return -1
  const recoveredAttempt = recoveredStep.attempt || 1
  return steps.findIndex(
    (step) =>
      workflowStepNodeName(step) === recoveredNodeName && (step.attempt || 1) === recoveredAttempt
  )
}

/** 选择结构化产物更完整的 DAG 快照，完成事件优先覆盖旧的中间快照。 */
function mergeDagGenerationSnapshot(
  current: DagGenerationSnapshot | undefined,
  recovered: DagGenerationSnapshot | undefined
): DagGenerationSnapshot | undefined {
  if (!current) return recovered
  if (!recovered) return current
  return dagGenerationOutputCount(recovered) >= dagGenerationOutputCount(current)
    ? recovered
    : current
}

/** 统计阶段级结构化产物数量，用于判断哪个历史快照更完整。 */
function dagGenerationOutputCount(snapshot: DagGenerationSnapshot): number {
  return snapshot.stages.reduce((count, stage) => count + (stage.output ? 1 : 0), 0)
}

/** 从完成事件或旧状态快照恢复工作区检查详情，并补齐缓存命中标记。 */
function completedWorkspaceInspection(
  workflow: WorkflowRunPayload | undefined
): ProcessStepRecord['workspaceInspection'] {
  if (!workflow) return undefined
  const event = [...workflow.events]
    .reverse()
    .find(
      (item) =>
        (item.nodeName === 'inspect_workspace' || item.nodeName === 'scan_workspace_code') &&
        item.type === 'workflow.node.completed'
    )
  const detail = event ? workflowEventDetail(event) : {}
  const stateDelta =
    event?.data?.stateDelta && typeof event.data.stateDelta === 'object'
      ? (event.data.stateDelta as Record<string, unknown>)
      : {}
  const snapshot = readWorkspaceInspectionSnapshot(
    detail.workspaceInspection ??
      stateDelta.workspaceInspection ??
      stateDelta.workspace_snapshot_summary ??
      workflow.state?.workspaceInspection ??
      workflow.state?.workspace_snapshot_summary ??
      workflow.result?.workspaceInspection ??
      workflow.result?.workspace_snapshot_summary
  )
  if (!snapshot) return undefined
  const eventTimeline = Array.isArray(stateDelta.timeline)
    ? stateDelta.timeline.filter((item): item is string => typeof item === 'string')
    : []
  return {
    ...snapshot,
    cacheHit:
      snapshot.cacheHit ||
      eventTimeline.includes('inspect_workspace:cache_hit') ||
      workflowTimeline(workflow).some((item) =>
        ['inspect_workspace:cache_hit', 'scan_workspace_code:cache_hit'].includes(item)
      )
  }
}

/** 为正式 Workflow 保留可读步骤，并在自由对话中隐藏快速修改内部执行轨迹。 */
export function processStepsForMessageDisplay(
  steps: ProcessStepRecord[] | undefined,
  workflow: WorkflowRunPayload | undefined
): ProcessStepRecord[] | undefined {
  const displaySteps = processStepsForDisplay(steps, workflow)
  if (!displaySteps?.length) return displaySteps

  // 自由对话需要展示工具和当前动作；正式 Workflow 则隐藏底层工具，避免重复渲染。
  if (isConversationWorkflow(workflow)) return displaySteps

  const stableSteps = displaySteps.filter((step) => step.kind !== 'tool' && step.kind !== 'command')
  return stableSteps
}

/** 判断 Workflow 是否属于由结构化卡片承载的规划产物生成流程。 */
export function isStructuredPlanningWorkflow(workflow: WorkflowRunPayload | undefined): boolean {
  if (!workflow || isConversationWorkflow(workflow)) return false
  const planningNodes = new Set(['product_planning', 'project_planning', 'technical_planning'])
  if (planningNodes.has(String(workflow.summary.phase || ''))) return true
  const clarificationCandidates = [
    workflow.summary.clarification,
    workflow.state?.clarification,
    workflow.result?.clarification
  ]
  if (
    clarificationCandidates.some((value) => {
      const mode = String((value as { mode?: string } | undefined)?.mode || '')
      return mode.includes('product_plan') || mode.includes('project_plan') || mode.includes('technical_plan')
    })
  ) {
    return true
  }
  return workflow.events.some((event) => planningNodes.has(String(event.nodeName || '')))
}

/** 隐藏结构化规划流程的原始 JSON 与重复 Workflow 摘要，同时保留真实回复内容。 */
export function workflowMessageContentForDisplay(
  content: string,
  workflow: WorkflowRunPayload | undefined,
  hasProcessSteps: boolean
): string {
  const normalizedContent = content.trim()
  if (!normalizedContent) return content

  // 自由对话的摘要就是助手正文，必须始终保留，避免过程步骤在结束时吞掉回复。
  if (isConversationWorkflow(workflow)) return content
  // 规划正文由确认卡、进度卡或错误卡展示，历史 session 中已保存的模型 JSON 也必须隐藏。
  if (isStructuredPlanningWorkflow(workflow)) return ''
  if (!hasProcessSteps) return content

  const normalizedSummary = workflow?.summary.message?.trim()
  if (normalizedSummary && normalizedContent === normalizedSummary) return ''

  const legacyStatusPattern =
    /^Workflow 等待用户确认\/补充：完成 \d+ 个节点，待确认问题 \d+ 个。(?:\s*预览地址：\S+。?)?$/
  return legacyStatusPattern.test(normalizedContent) ? '' : content
}

/** 从已持久化的节点完成事件重建基础步骤，兼容旧 session 未保存 processSteps 的情况。 */
function completedWorkflowProcessSteps(
  workflow: WorkflowRunPayload | undefined
): ProcessStepRecord[] | undefined {
  if (!workflow) return undefined
  const stepsById = new Map<string, ProcessStepRecord>()
  const attemptsByNode = new Map<string, number>()
  for (const [index, event] of workflow.events.entries()) {
    if (event.type !== 'workflow.node.completed' || !event.nodeName) continue
    const inferredAttempt = (attemptsByNode.get(event.nodeName) || 0) + 1
    const attempt = typeof event.attempt === 'number' ? event.attempt : inferredAttempt
    attemptsByNode.set(event.nodeName, Math.max(inferredAttempt, attempt))
    const status = processStepStatus(event.status)
    const label = event.node?.label || event.nodeName
    const detail = workflowEventDetail(event)
    const stepId =
      attempt === 1 ? `workflow:${event.nodeName}` : `workflow:${event.nodeName}:${attempt}`
    const checks =
      event.nodeName === 'integration_test'
        ? readIntegrationTestChecks(detail.testReport)
        : undefined
    const buildExecutionSlice =
      event.nodeName === 'build' &&
      detail.buildExecutionSlice &&
      typeof detail.buildExecutionSlice === 'object'
        ? (detail.buildExecutionSlice as ProcessStepRecord['buildExecutionSlice'])
        : undefined
    const stateDelta =
      event.data?.stateDelta && typeof event.data.stateDelta === 'object'
        ? (event.data.stateDelta as Record<string, unknown>)
        : {}
    const dagGeneration =
      event.nodeName === 'prepare_build_tasks'
        ? completedDagGenerationSnapshot(detail, stateDelta, workflow)
        : undefined
    const projectPlanUpdate =
      event.nodeName === 'detail_confirmation'
        ? readProjectPlanUpdate(detail.projectPlanUpdate)
        : undefined
    const step: ProcessStepRecord = {
      id: stepId,
      kind: 'workflow',
      status,
      title: `${processStepTitlePrefix(status)} ${label}`,
      detail: event.message || '',
      sequence: index + 1,
      nodeName: event.nodeName,
      attempt,
      iterationKind: event.iterationKind,
      ...(checks ? { checks } : {}),
      ...(buildExecutionSlice ? { buildExecutionSlice } : {}),
      ...(dagGeneration ? { dagGeneration } : {}),
      ...(projectPlanUpdate ? { projectPlanUpdate } : {})
    }
    stepsById.set(step.id, step)
  }
  if (stepsById.size === 0) {
    const timeline = workflowTimeline(workflow)
    for (const [index, nodeName] of timeline.entries()) {
      const label = workflowNodeLabel(nodeName, workflow)
      if (!label) continue
      const stepId = `workflow:${nodeName}`
      // 旧 timeline 可能重复记录同一节点；保留首次出现位置，避免历史步骤发生跳序。
      if (stepsById.has(stepId)) continue
      stepsById.set(stepId, {
        id: stepId,
        kind: 'workflow',
        status: 'completed',
        title: `已完成 ${label}`,
        detail: '',
        sequence: index + 1,
        nodeName,
        attempt: 1
      })
    }
  }
  const steps = [...stepsById.values()]
  return steps.length > 0 ? steps : undefined
}

/** 从完成事件、状态和结果三个兼容来源中恢复最完整的 DAG 产物快照。 */
function completedDagGenerationSnapshot(
  detail: Record<string, unknown>,
  stateDelta: Record<string, unknown>,
  workflow: WorkflowRunPayload
): DagGenerationSnapshot | undefined {
  const candidates = [
    detail.dagGeneration,
    detail.dag_generation_progress,
    stateDelta.dagGeneration,
    stateDelta.dag_generation_progress,
    workflow.state?.dagGeneration,
    workflow.state?.dag_generation_progress,
    workflow.result?.dagGeneration,
    workflow.result?.dag_generation_progress
  ]
    .map((candidate) => readDagGenerationSnapshot(candidate))
    .filter((candidate): candidate is DagGenerationSnapshot => Boolean(candidate))

  return candidates.reduce<DagGenerationSnapshot | undefined>(
    (current, candidate) => mergeDagGenerationSnapshot(current, candidate),
    undefined
  )
}

/** 返回节点轮次对应的稳定步骤 ID。 */
function processStepId(nodeName: string, attempt: number): string {
  return attempt === 1 ? `workflow:${nodeName}` : `workflow:${nodeName}:${attempt}`
}

/** 按 Workflow 阶段语义排序展示步骤，避免异步事件到达顺序把数据库检查放到 DAG 后面。 */
function sortProcessStepsForDisplay(
  steps: ProcessStepRecord[] | undefined
): ProcessStepRecord[] | undefined {
  if (!steps?.length) return steps
  return [...steps].sort((left, right) => {
    const semanticOrder = databaseContextBeforeDagOrder(left, right)
    if (semanticOrder !== 0) return semanticOrder
    return left.sequence - right.sequence
  })
}

/** 只修正数据库上下文检查与 DAG 生成的相对顺序，其它轮次保持后端 sequence。 */
function databaseContextBeforeDagOrder(left: ProcessStepRecord, right: ProcessStepRecord): number {
  const leftNodeName = workflowStepNodeName(left)
  const rightNodeName = workflowStepNodeName(right)
  if (leftNodeName === 'inspect_database_context' && rightNodeName === 'prepare_build_tasks') {
    return -1
  }
  if (leftNodeName === 'prepare_build_tasks' && rightNodeName === 'inspect_database_context') {
    return 1
  }
  return 0
}

/** 从实时或历史步骤中提取稳定 Workflow 节点名。 */
function workflowStepNodeName(step: ProcessStepRecord): string {
  if (step.kind !== 'workflow') return ''
  const nodeName = step.nodeName || step.id.replace(/^workflow:/, '').split(':')[0]
  return nodeName
}

/** 根据 Workflow 目标类型动态返回节点展示名称，兼容 endpoint 详细设计历史。 */
function workflowNodeLabel(nodeName: string, workflow: WorkflowRunPayload): string | undefined {
  const detailTargetType =
    workflow.state?.detailTargetType ||
    workflow.result?.detailTargetType ||
    workflow.summary.clarification?.review?.summary?.detailTargetType
  if (nodeName === 'detail_confirmation' && detailTargetType === 'endpoint') {
    return '接口细节确认'
  }
  if (nodeName === 'detail_confirmation' && detailTargetType === 'entity') {
    return '实体设计'
  }
  return WORKFLOW_NODE_LABELS[nodeName]
}

/** 将历史 Workflow 事件状态规整为前端步骤状态。 */
function processStepStatus(status: string | undefined): ProcessStepRecord['status'] {
  if (status === 'failed') return 'failed'
  if (status === 'requires_user_input') return 'requires_user_input'
  return 'completed'
}

/** 返回历史步骤终态对应的标题前缀。 */
function processStepTitlePrefix(status: ProcessStepRecord['status']): string {
  if (status === 'failed') return '执行失败'
  if (status === 'requires_user_input') return '等待确认'
  return '已完成'
}

/** 安全读取 Workflow 节点事件中的结构化 detail。 */
function workflowEventDetail(event: WorkflowRunPayload['events'][number]): Record<string, unknown> {
  const detail = event.data?.detail
  return detail && typeof detail === 'object' ? (detail as Record<string, unknown>) : {}
}

/** 从新旧 Workflow 快照字段读取节点时间线。 */
function workflowTimeline(workflow: WorkflowRunPayload): string[] {
  const stateTimeline = workflow.state?.timeline
  const resultTimeline = workflow.result?.timeline
  const value = Array.isArray(stateTimeline)
    ? stateTimeline
    : Array.isArray(resultTimeline)
      ? resultTimeline
      : []
  return value.filter((item): item is string => typeof item === 'string')
}

/** 从 integration_test 完成事件或状态快照读取最终检查清单。 */
function completedIntegrationTestChecks(
  workflow: WorkflowRunPayload | undefined
): ReturnType<typeof readIntegrationTestChecks> {
  if (!workflow) return undefined
  const event = [...workflow.events]
    .reverse()
    .find((item) => item.nodeName === 'integration_test' && item.type === 'workflow.node.completed')
  const eventDetail =
    event?.data?.detail && typeof event.data.detail === 'object'
      ? (event.data.detail as Record<string, unknown>)
      : undefined
  const eventChecks = readIntegrationTestChecks(eventDetail?.testReport ?? event?.data?.testReport)
  if (eventChecks?.length) return eventChecks
  return readIntegrationTestChecks(
    workflow.state?.testReport ??
      workflow.state?.test_report ??
      workflow.result?.testReport ??
      workflow.result?.test_report
  )
}

/** 从最新页面细节确认完成事件中读取只读项目计划更新快照。 */
function completedProjectPlanUpdate(workflow: WorkflowRunPayload | undefined):
  | {
      attempt?: number
      snapshot: NonNullable<ProcessStepRecord['projectPlanUpdate']>
    }
  | undefined {
  if (!workflow) return undefined
  const event = [...workflow.events]
    .reverse()
    .find(
      (item) => item.nodeName === 'detail_confirmation' && item.type === 'workflow.node.completed'
    )
  if (!event) return undefined
  const detail = workflowEventDetail(event)
  const snapshot = readProjectPlanUpdate(detail.projectPlanUpdate)
  if (!snapshot) return undefined
  return {
    attempt: typeof event.attempt === 'number' ? event.attempt : undefined,
    snapshot
  }
}

/** 按稳定检查 id 合并实时与完成态快照，完成态覆盖同名检查的最终结果。 */
function mergeIntegrationTestChecks(
  current: ProcessStepRecord['checks'],
  finalChecks: NonNullable<ReturnType<typeof readIntegrationTestChecks>>
): NonNullable<ProcessStepRecord['checks']> {
  const checksById = new Map(current?.map((check) => [check.id, check]) ?? [])
  for (const check of finalChecks) checksById.set(check.id, check)
  return [...checksById.values()]
}
