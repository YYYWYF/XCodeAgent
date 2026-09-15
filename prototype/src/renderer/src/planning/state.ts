import type { ApplicationLifecycleStage } from '../typings'
import { synchronizePlanningReferences } from './seed'
import type {
  InitializationPlanningEvent,
  InitializationPlanningRecord,
  FormalArtifactKey
} from './model'

const sources: Record<InitializationPlanningEvent['type'], ApplicationLifecycleStage[]> = {
  start_design: ['collecting_requirement', 'analyzing_requirement'],
  start_requirement_document: ['analyzing_requirement'],
  requirement_document_ready: ['analyzing_requirement', 'generating_requirement_document'],
  revise_requirement_document: [
    'awaiting_requirement_document_confirmation',
    'awaiting_ui_design_confirmation',
    'awaiting_planning_stage_entry'
  ],
  confirm_requirement_document: ['awaiting_requirement_document_confirmation'],
  ui_designs_ready: ['generating_ui_designs'],
  revise_ui_designs: ['awaiting_ui_design_confirmation'],
  confirm_ui_designs: ['awaiting_ui_design_confirmation'],
  skip_ui_designs: ['awaiting_ui_design_confirmation'],
  enter_planning: ['awaiting_planning_stage_entry'],
  technical_plan_ready: ['generating_technical_plan'],
  revise_technical_plan: ['awaiting_technical_plan_confirmation'],
  confirm_technical_plan: ['awaiting_technical_plan_confirmation'],
  template_generation_completed: ['generating_application_template_files'],
  confirm_development_entry: ['awaiting_development_entry']
}

/** 上游修改时撤销所有下游产物确认，后续必须重新生成并逐门确认。 */
export function invalidatePlanningArtifacts(
  record: InitializationPlanningRecord,
  keys: FormalArtifactKey[]
): void {
  for (const key of keys) {
    record.artifactStatus[key] = 'draft'
    delete record.documents[key]
  }
  if (keys.includes('ui-designs')) {
    record.artifacts.uiDesigns.confirmation_status = 'pending_user_confirmation'
    record.artifacts.uiDesigns.pages.forEach((page) => {
      page.status = 'queued'
    })
  }
}

/** 校验每次业务转换，产物与页面状态原子更新，杜绝已确认新稿直接推进。 */
export function transitionInitializationPlanning(
  current: InitializationPlanningRecord,
  event: InitializationPlanningEvent
): InitializationPlanningRecord {
  if (!sources[event.type].includes(current.stage)) {
    throw new Error('当前阶段已变化，请使用最新的产物操作。')
  }
  const next = structuredClone(current)
  next.revision += 1
  next.updatedAt = new Date().toISOString()
  next.operationStatus = 'idle'
  delete next.error
  const status = next.artifactStatus
  switch (event.type) {
    case 'start_design':
      next.stage = 'analyzing_requirement'
      break
    case 'start_requirement_document':
      next.stage = 'generating_requirement_document'
      break
    case 'requirement_document_ready':
      next.stage = 'awaiting_requirement_document_confirmation'
      status['requirement-spec'] = status['product-plan'] = 'pending'
      break
    case 'revise_requirement_document':
      invalidatePlanningArtifacts(next, [
        'requirement-spec',
        'product-plan',
        'ui-designs',
        'technical-plan'
      ])
      if (event.feedback) {
        next.feedback.push(event.feedback)
        next.artifacts.requirementSpec.change_request = event.feedback
        next.artifacts.requirementSpec.business_constraints = [
          ...(next.artifacts.requirementSpec.business_constraints || []),
          event.feedback
        ]
        next.artifacts.productPlan.change_request = event.feedback
      }
      next.stage = 'generating_requirement_document'
      break
    case 'confirm_requirement_document':
      if (status['requirement-spec'] !== 'pending' || status['product-plan'] !== 'pending')
        throw new Error('请先完成需求规格说明书。')
      status['requirement-spec'] = status['product-plan'] = 'confirmed'
      synchronizePlanningReferences(next.artifacts)
      next.stage = 'generating_ui_designs'
      status['ui-designs'] = 'pending'
      break
    case 'ui_designs_ready':
      // 首轮生成只准备页面清单并打开确认门，不产出设计稿——右侧与对话卡都在等待版式选择；
      // 用户选完模板后的再生成（templates_selected 已置位）才把页面标记为已生成。
      if (!next.artifacts.uiDesigns.templates_selected) {
        next.artifacts.uiDesigns.pages.forEach((page) => {
          if (page.status !== 'confirmed') page.status = 'queued'
        })
      } else {
        next.artifacts.uiDesigns.pages.forEach((page) => {
          if (page.status !== 'confirmed') page.status = 'generated'
        })
      }
      next.stage = 'awaiting_ui_design_confirmation'
      status['ui-designs'] = 'pending'
      break
    case 'revise_ui_designs': {
      // 批量选模板：一次为全部待确认页面定版式，统一交后台重画。
      const targetMap = new Map(event.pages.map((item) => [item.pageId, item.template] as const))
      if (targetMap.size === 0) throw new Error('请先为页面选择版式模板。')
      for (const pageId of targetMap.keys()) {
        if (!next.artifacts.uiDesigns.pages.some((page) => page.pageId === pageId))
          throw new Error('页面不存在。')
      }
      invalidatePlanningArtifacts(next, ['technical-plan'])
      next.artifacts.uiDesigns.confirmation_status = 'pending_user_confirmation'
      // 有版式选择被提交，本轮生成就会真正产出设计稿。
      next.artifacts.uiDesigns.templates_selected = true
      next.artifacts.uiDesigns.pages.forEach((page) => {
        const template = targetMap.get(page.pageId)
        if (!template) return
        page.status = 'queued'
        page.variant = (page.variant || 0) + 1
        page.template = template
      })
      delete next.documents['ui-designs']
      status['ui-designs'] = 'pending'
      next.stage = 'generating_ui_designs'
      break
    }
    case 'confirm_ui_designs': {
      const pages = next.artifacts.uiDesigns.pages
      if (!pages.length || pages.some((page) => !['generated', 'confirmed'].includes(page.status)))
        throw new Error('请等待页面生成成功后再确认。')
      pages.forEach((page) => {
        page.status = 'confirmed'
      })
      status['ui-designs'] = 'confirmed'
      next.artifacts.uiDesigns.confirmation_status = 'confirmed'
      next.stage = 'awaiting_planning_stage_entry'
      break
    }
    case 'skip_ui_designs':
      status['ui-designs'] = 'skipped'
      next.artifacts.uiDesigns.confirmation_status = 'skipped'
      next.artifacts.uiDesigns.pages = []
      next.stage = 'awaiting_planning_stage_entry'
      break
    case 'enter_planning':
      if (
        status['requirement-spec'] !== 'confirmed' ||
        status['product-plan'] !== 'confirmed' ||
        !['confirmed', 'skipped'].includes(status['ui-designs'])
      )
        throw new Error('请先确认需求规格说明书与本次 UI 设计。')
      next.stage = 'generating_technical_plan'
      break
    case 'technical_plan_ready':
      next.stage = 'awaiting_technical_plan_confirmation'
      status['technical-plan'] = 'pending'
      break
    case 'revise_technical_plan':
      invalidatePlanningArtifacts(next, ['technical-plan'])
      if (event.feedback) {
        next.feedback.push(event.feedback)
        next.artifacts.technicalPlan.change_request = event.feedback
      }
      next.stage = 'generating_technical_plan'
      break
    case 'confirm_technical_plan':
      if (status['technical-plan'] !== 'pending') throw new Error('请先生成技术规划方案。')
      status['technical-plan'] = 'confirmed'
      next.stage = 'generating_application_template_files'
      break
    case 'template_generation_completed':
      next.stage = 'awaiting_development_entry'
      break
    case 'confirm_development_entry':
      next.stage = 'ready_for_workbench'
      break
  }
  next.artifacts.requirementSpec.confirmation_status =
    status['requirement-spec'] === 'confirmed' ? 'confirmed' : 'pending_user_confirmation'
  next.artifacts.productPlan.confirmation_status =
    status['product-plan'] === 'confirmed' ? 'confirmed' : 'pending_user_confirmation'
  next.artifacts.technicalPlan.confirmation_status =
    status['technical-plan'] === 'confirmed' ? 'confirmed' : 'pending_user_confirmation'
  return next
}
