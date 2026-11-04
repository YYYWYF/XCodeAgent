import type { ApplicationLifecycle, WorkflowRunPayload } from '../../typings'

export type PlanningTaskBlock = {
  phase: 'product' | 'planning'
  message: string
  actionLabel: string
}

const PLAN_STAGES = new Set([
  'generating_technical_plan', 'awaiting_technical_plan_confirmation',
  'generating_application_template_files', 'application_template_generation_failed'
])

/** 与后端开发准入保持一致，未完成的需求或计划不能因浏览开发阶段而放行。 */
export function planningTaskBlock(lifecycle?: ApplicationLifecycle): PlanningTaskBlock | undefined {
  if (!lifecycle || lifecycle.initialization.stage === 'ready_for_workbench') return undefined
  const planning = PLAN_STAGES.has(lifecycle.initialization.stage)
  const label = planning ? '计划' : '需求'
  const task = planning ? '计划' : '需求任务'
  const status = ['cancelled', 'failed'].includes(lifecycle.initialization.status) ? '尚未完成' : '正在运行'
  return { phase: planning ? 'planning' : 'product',
    message: `${label}阶段${status}，请先完成或结束当前${task}`, actionLabel: `返回${label}阶段` }
}

/** 阶段准入失败只显示后端业务说明，传输层异常类型不作为用户提示。 */
export function planningAdmissionFailureMessage(workflow?: WorkflowRunPayload): string | undefined {
  if (workflow?.summary.status !== 'failed' || ![
    'APPLICATION_PLAN_IN_PROGRESS', 'APPLICATION_REQUIREMENT_IN_PROGRESS'
  ].includes(String(workflow.summary.errorCode))) return undefined
  return workflow.summary.message
}
