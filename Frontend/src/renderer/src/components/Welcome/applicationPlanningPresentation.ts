import type { WorkflowClarification, WorkflowRunPayload } from '../../typings'
import type { ApplicationPlanningProgressEvent } from './ApplicationPlanningProgress'
import {
  planningRequirementsDocumentGenerating,
  planningWorkflowLifecycleStage,
  planningWorkflowPhase
} from './planningWorkflowState'

const phaseOrder = [
  'requirements',
  'product_planning',
  'ui_confirmation',
  'planning_stage_entry',
  'technical_planning'
]

const phaseProgress: Record<
  string,
  { active: number; complete: number; message: string; title: string }
> = {
  requirements: {
    active: 10,
    complete: 20,
    message: '正在分析需求并识别待补充信息…',
    title: '正在分析需求'
  },
  product_planning: {
    active: 30,
    complete: 40,
    message: '正在生成页面目标、核心操作与产品验收标准…',
    title: '正在整理需求'
  },
  ui_confirmation: {
    active: 52,
    complete: 65,
    message: '正在为各页面生成设计稿…',
    title: '正在生成UI设计稿'
  },
  planning_stage_entry: {
    active: 68,
    complete: 68,
    message: '设计阶段已完成，等待进入计划阶段…',
    title: '等待进入计划阶段'
  },
  technical_planning: {
    active: 78,
    complete: 100,
    message: '正在生成 API、数据与页面实现契约…',
    title: '正在生成技术规划'
  }
}

// 优先读取已确认 ProductPlan 的页面数，用于 UI 生成期间渲染未就绪骨架。
export function planningUiDesignPageTotal(workflow?: WorkflowRunPayload): number {
  if (!workflow) return 0
  for (const source of [workflow.result, workflow.state]) {
    const productPlan = source?.product_plan
    if (productPlan && typeof productPlan === 'object' && !Array.isArray(productPlan)) {
      const productPages = (productPlan as Record<string, unknown>).pages
      if (Array.isArray(productPages)) return productPages.length
    }
    const spec = source?.requirement_spec
    if (spec && typeof spec === 'object' && !Array.isArray(spec)) {
      const pages = (spec as Record<string, unknown>).pages
      if (Array.isArray(pages)) return pages.length
    }
  }
  return 0
}

// 根据当前节点计算创建规划进度条的高亮位置。
function workflowStep(workflow?: WorkflowRunPayload): number {
  const phase = planningWorkflowPhase(workflow)
  const index = phaseOrder.indexOf(phase)
  return index >= 0 ? index : 0
}

// 判断当前是否已经进入技术规划确认，便于切换成完整的技术规划工作区壳层。
export function technicalPlanConfirmationReady(workflow?: WorkflowRunPayload): boolean {
  const clarifications = [
    workflow?.summary.clarification,
    workflow?.state?.clarification,
    workflow?.result?.clarification
  ]
  return clarifications.some((clarification) => {
    if (!clarification || typeof clarification !== 'object') return false
    const mode = (clarification as WorkflowClarification).mode
    return mode === 'technical_plan_confirmation'
  })
}

// 将独立 Workflow 的当前节点转换为原页面规划进度组件需要的阶段时间线。
export function workflowProgressEvents(
  workflow?: WorkflowRunPayload,
  preparingTemplate = false
): ApplicationPlanningProgressEvent[] {
  if (!workflow) return []
  const currentIndex = workflowStep(workflow)
  const finished = workflow.summary.status === 'completed'
  const events = phaseOrder.slice(0, currentIndex + 1).map((stage, index) => {
    const meta =
      stage === 'product_planning' &&
      planningRequirementsDocumentGenerating(workflow, planningWorkflowLifecycleStage(workflow))
        ? {
            ...phaseProgress.requirements,
            message: '正在生成需求文档…',
            title: '正在生成需求文档'
          }
        : phaseProgress[stage]
    const completed = index < currentIndex || (finished && index === currentIndex)
    return {
      stage,
      percent: completed ? meta.complete : meta.active,
      message: completed ? `${meta.title.replace('正在', '')}已完成` : meta.message,
      detail:
        index === currentIndex && workflow.summary.message
          ? String(workflow.summary.message)
          : undefined
    }
  })
  if (preparingTemplate) {
    events.push({
      stage: 'application_template',
      percent: 92,
      message: '正在下载模板代码并准备工作区…',
      detail: undefined
    })
  }
  return events
}

// 返回当前节点在动态进度卡上的标题与兜底说明。
export function workflowProgressCopy(workflow?: WorkflowRunPayload): { fallback: string; title: string } {
  const stage = phaseOrder[workflowStep(workflow)]
  if (stage === 'product_planning' && planningRequirementsDocumentGenerating(workflow)) {
    return { fallback: '正在生成需求文档…', title: '正在生成需求文档' }
  }
  const meta = phaseProgress[stage] || phaseProgress.requirements
  return { fallback: meta.message, title: meta.title }
}
