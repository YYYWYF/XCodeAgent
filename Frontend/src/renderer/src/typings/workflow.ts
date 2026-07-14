import type { WorkspaceCodeChangeSet } from './codeChanges'

export type WorkflowEvent = {
  type: string
  runId?: string
  threadId?: string
  nodeName?: string
  node?: {
    id?: string
    label?: string
  }
  status?: string
  message?: string
  data?: Record<string, unknown>
  timestamp?: string
}

export type WorkflowSummary = {
  status?: string
  message?: string
  phase?: string
  artifacts?: Record<string, string>
  clarification?: WorkflowClarification
  [key: string]: unknown
}

export type WorkflowClarificationQuestion = {
  id?: string
  header?: string
  question?: string
  type?: 'choice' | 'text' | 'yesno'
  dimension?: string
  default_assumption?: string
  placeholder?: string
  multiSelect?: boolean
  allowOther?: boolean
  options?: Array<{
    label?: string
    description?: string
    value?: string
  }>
}

export type WorkflowClarificationChoiceAnswer = {
  selected: string | string[]
  other?: string
}

export type WorkflowDetailReviewTarget = {
  target_type: 'page' | 'data_source'
  target_id: string
  name?: string
  path?: string
  page_goal?: string
  basic_layout?: Record<string, unknown>
  layout_design?: Record<string, unknown>
  interactions?: string[]
  state_feedback?: Array<Record<string, unknown>>
  operation_interactions?: Array<Record<string, unknown>>
  operation_visibility?: Array<Record<string, unknown>>
  page_navigation?: Array<Record<string, unknown>>
  permissions?: string[]
  states?: string[]
  api_dependencies?: Array<Record<string, unknown>>
  data_sources?: Array<Record<string, unknown>>
  response_bindings?: Array<Record<string, unknown>>
  acceptance_criteria?: string[]
  source_type?: string
  entities?: string[]
  schema_refs?: string[]
  relationships?: string[]
  validation_rules?: string[]
  seed_strategy?: string
  api_contracts?: Array<Record<string, unknown>>
  dependent_pages?: Array<Record<string, unknown>>
}

export type WorkflowDetailReview = {
  pages?: WorkflowDetailReviewTarget[]
  data_sources?: WorkflowDetailReviewTarget[]
  summary?: {
    page_count?: number
    data_source_count?: number
    api_contract_count?: number
  }
}

export type WorkflowDetailReviewSubmission = {
  review_status: 'confirmed'
  target_changes: Array<{
    target_type: 'page' | 'data_source'
    target_id: string
    changes: Record<string, unknown>
  }>
  overall_note?: string
}

export type WorkflowClarificationAnswer =
  | string
  | string[]
  | WorkflowClarificationChoiceAnswer
  | WorkflowDetailReviewSubmission

export type WorkflowClarificationAnswers = Record<
  string,
  WorkflowClarificationAnswer
>

export type WorkflowClarificationSelectionGroup = {
  type?: string
  title?: string
  items?: Array<{
    id?: string
    label?: string
    name?: string
    description?: string
  }>
}

export type WorkflowClarification = {
  mode?: string
  status?: string
  question_schema?: string
  message?: string
  questions?: WorkflowClarificationQuestion[]
  assumptions?: string[]
  all_unresolved_dimensions?: string[]
  selection_groups?: WorkflowClarificationSelectionGroup[]
  context?: Record<string, unknown>
  review?: WorkflowDetailReview
  [key: string]: unknown
}

export type WorkflowConfirmationArtifact = {
  id: 'requirement_spec' | 'project_plan'
  name: string
  path: string
  format: 'markdown'
  content: string
}

export type WorkflowRunPayload = {
  runId: string
  threadId: string
  summary: WorkflowSummary
  events: WorkflowEvent[]
  confirmationArtifact?: WorkflowConfirmationArtifact
  codeChanges?: WorkspaceCodeChangeSet
  state?: Record<string, unknown>
  result?: Record<string, unknown>
}

export type WorkflowDebugOptions = {
  enabled: boolean
  resumeFrom?: string
  requirementSpecPath?: string
  projectPlanPath?: string
  workspaceSnapshotPath?: string
  buildTaskPlanPath?: string
}
