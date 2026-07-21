import type { WorkflowRunPayload } from '../typings'
import { readIntegrationTestChecks } from './agUiAgent'
import type { ProcessStepRecord } from './agUiAgent'

const WORKFLOW_NODE_LABELS: Record<string, string> = {
  detail_confirmation: '页面细节确认',
  inspect_workspace: '工作区快照检查',
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
  const displaySteps = steps?.length ? steps : completedWorkflowProcessSteps(workflow)
  if (!displaySteps?.length) return displaySteps
  const finalChecks = completedIntegrationTestChecks(workflow)
  if (!finalChecks?.length) return displaySteps

  return displaySteps.map((step) => {
    if (step.id !== 'workflow:integration_test') return step
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
  for (const [index, event] of workflow.events.entries()) {
    if (event.type !== 'workflow.node.completed' || !event.nodeName) continue
    const status = event.status === 'failed' ? 'failed' : 'completed'
    const label = event.node?.label || event.nodeName
    const step: ProcessStepRecord = {
      id: `workflow:${event.nodeName}`,
      kind: 'workflow',
      status,
      title: `${status === 'failed' ? '执行失败' : '已完成'} ${label}`,
      detail: event.message || '',
      sequence: index + 1
    }
    stepsById.set(step.id, step)
  }
  if (stepsById.size === 0) {
    const timeline = workflowTimeline(workflow)
    for (const [index, nodeName] of timeline.entries()) {
      const label = WORKFLOW_NODE_LABELS[nodeName]
      if (!label) continue
      stepsById.set(`workflow:${nodeName}`, {
        id: `workflow:${nodeName}`,
        kind: 'workflow',
        status: 'completed',
        title: `已完成 ${label}`,
        detail: '',
        sequence: index + 1
      })
    }
  }
  const steps = [...stepsById.values()]
  return steps.length > 0 ? steps : undefined
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
