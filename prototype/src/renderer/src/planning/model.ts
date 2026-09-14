import type { ApplicationLifecycleStage, WorkflowRunPayload } from '../typings'

export type FormalArtifactKey =
  | 'requirement-spec'
  | 'product-plan'
  | 'ui-designs'
  | 'technical-plan'
export type FormalArtifactStatus = 'draft' | 'pending' | 'confirmed' | 'skipped'
export type UiDesignPage = {
  pageId: string
  name: string
  path: string
  page_key: string
  template: string
  status: 'queued' | 'generating' | 'generated' | 'confirmed' | 'failed'
  description: string
  sections: string[]
  accent?: 'violet' | 'indigo'
  variant?: number
}
export type InitializationPlanningArtifacts = {
  requirementSpec: Record<string, any>
  productPlan: Record<string, any>
  uiDesigns: {
    schema_version: 'ui-manifest.v3'
    confirmation_status: 'pending_user_confirmation' | 'confirmed' | 'skipped'
    /** 用户是否已完成一轮版式选择：首轮生成只准备页面清单，选完模板后才真正产出设计稿。 */
    templates_selected?: boolean
    pages: UiDesignPage[]
  }
  technicalPlan: Record<string, any>
}
export type InitializationPlanningSeed = InitializationPlanningArtifacts
export type InitializationPlanningRecord = {
  applicationId: string
  applicationName: string
  versionId: string
  workspaceRoot: string
  revision: number
  stage: ApplicationLifecycleStage
  artifactStatus: Record<FormalArtifactKey, FormalArtifactStatus>
  artifacts: InitializationPlanningArtifacts
  documents: Partial<Record<FormalArtifactKey, string>>
  clarificationAnswers: Record<string, unknown>
  feedback: string[]
  operationStatus: 'idle' | 'running' | 'failed' | 'cancelled'
  error?: string
  workflow?: WorkflowRunPayload
  updatedAt: string
}
export type InitializationPlanningEvent =
  | { type: 'start_design' }
  | { type: 'start_requirement_document' }
  | { type: 'requirement_document_ready' }
  | { type: 'revise_requirement_document'; feedback?: string }
  | { type: 'confirm_requirement_document' }
  | { type: 'ui_designs_ready' }
  | { type: 'revise_ui_designs'; pages: Array<{ pageId: string; template: string }> }
  | { type: 'confirm_ui_designs' }
  | { type: 'skip_ui_designs' }
  | { type: 'enter_planning' }
  | { type: 'technical_plan_ready' }
  | { type: 'revise_technical_plan'; feedback?: string }
  | { type: 'confirm_technical_plan' }
  | { type: 'template_generation_completed' }
  | { type: 'confirm_development_entry' }

export type PlanningAction = {
  // edit_requirements 仅由确认卡发出、在 AiChatPanel 拦截为右侧原位表单切换，
  // 永远不会作为工作流答案提交，因此规划剧本中没有对应分支。
  action:
    | 'confirm'
    | 'revise'
    | 'save_document'
    | 'save_requirements'
    | 'edit_requirements'
    | 'select_template'
    | 'retry'
  form?: import('./requirements').RequirementFormDraft
  artifactKey?: FormalArtifactKey
  /** 选模板的批量形态：一次为全部待确认页面定版式，统一交后台重画、右侧统一呈现。 */
  pages?: Array<{ pageId: string; template: string }>
  markdown?: string
  feedback?: string
}

/** 为当前正式产物版本生成确定性门禁身份，过期卡不能确认新的产物。 */
export function planningGate(record: InitializationPlanningRecord): string {
  return `${record.applicationId}:${record.versionId}:${record.stage}:${record.revision}`
}
