import type { WorkflowRunPayload } from '../typings'
import { readDagGenerationSnapshot, readIntegrationTestChecks } from './agUiAgent'
import type { ProcessStepRecord } from './agUiAgent'

const WORKFLOW_NODE_LABELS: Record<string, string> = {
  detail_confirmation: '页面细节确认',
  inspect_workspace: '工作区快照检查',
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
  const displaySteps = sortProcessStepsForDisplay(
    steps?.length ? steps : completedWorkflowProcessSteps(workflow)
  )
  if (!displaySteps?.length) return displaySteps
  const finalChecks = completedIntegrationTestChecks(workflow)
  if (!finalChecks?.length) return displaySteps

  const latestTestStepId = [...displaySteps]
    .reverse()
    .find(
      (step) =>
        step.nodeName === 'integration_test' || step.id.startsWith('workflow:integration_test')
    )?.id
  return displaySteps.map((step) => {
    if (step.id !== latestTestStepId) return step
    return {
      ...step,
      checks: mergeIntegrationTestChecks(step.checks, finalChecks)
    }
  })
}

/** 在已有结构化步骤时隐藏后端生成的重复 Workflow 摘要，同时保留真实回复内容。 */
export function workflowMessageContentForDisplay(
  content: string,
  workflow: WorkflowRunPayload | undefined,
  hasProcessSteps: boolean
): string {
  const normalizedContent = content.trim()
  if (!normalizedContent || !hasProcessSteps) return content

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
    const dagGeneration =
      event.nodeName === 'prepare_build_tasks'
        ? readDagGenerationSnapshot(
            detail.dagGeneration ?? workflow.state?.dagGeneration ?? workflow.result?.dagGeneration
          )
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
      ...(dagGeneration ? { dagGeneration } : {})
    }
    stepsById.set(step.id, step)
  }
  if (stepsById.size === 0) {
    const timeline = workflowTimeline(workflow)
    for (const [index, nodeName] of timeline.entries()) {
      const label = workflowNodeLabel(nodeName, workflow)
      if (!label) continue
      stepsById.set(`workflow:${nodeName}`, {
        id: `workflow:${nodeName}`,
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
function databaseContextBeforeDagOrder(
  left: ProcessStepRecord,
  right: ProcessStepRecord
): number {
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
function workflowNodeLabel(
  nodeName: string,
  workflow: WorkflowRunPayload
): string | undefined {
  const detailTargetType =
    workflow.state?.detailTargetType ||
    workflow.result?.detailTargetType ||
    workflow.summary.clarification?.review?.summary?.detailTargetType
  if (nodeName === 'detail_confirmation' && detailTargetType === 'endpoint') {
    return '接口细节确认'
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

/** 按稳定检查 id 合并实时与完成态快照，完成态覆盖同名检查的最终结果。 */
function mergeIntegrationTestChecks(
  current: ProcessStepRecord['checks'],
  finalChecks: NonNullable<ReturnType<typeof readIntegrationTestChecks>>
): NonNullable<ProcessStepRecord['checks']> {
  const checksById = new Map(current?.map((check) => [check.id, check]) ?? [])
  for (const check of finalChecks) checksById.set(check.id, check)
  return [...checksById.values()]
}
