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
  attempt?: number
  iterationKind?: string
}

export type WorkflowSummary = {
  status?: string
  message?: string
  phase?: string
  previewUrl?: string
  launchResult?: WorkflowLaunchResult
  acceptanceRequest?: WorkflowAcceptanceRequest
  artifacts?: Record<string, string>
  clarification?: WorkflowClarification
  [key: string]: unknown
}

export type WorkflowLaunchResult = {
  status?: string
  message?: string
  preview_url?: string
  server?: Record<string, unknown>
  [key: string]: unknown
}

export type WorkflowAcceptanceRequest = {
  status?: string
  message?: string
  preview_url?: string
  server?: Record<string, unknown>
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
    missingSelectedPagePlan?: boolean
    missingSelectedDataSourcePlan?: boolean
    selectedPageId?: string
    selectedDataSourceId?: string
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

export type WorkflowRequirementSpecEdit = Record<string, unknown>

export type WorkflowClarificationAnswer =
  | string
  | string[]
  | WorkflowClarificationChoiceAnswer
  | WorkflowDetailReviewSubmission
  | WorkflowRequirementSpecEdit

export type WorkflowClarificationAnswers = Record<string, WorkflowClarificationAnswer>

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

export type ApplicationLifecycleStage =
  | 'collecting_requirement'
  | 'analyzing_requirement'
  | 'awaiting_requirement_clarification'
  | 'generating_requirement_spec'
  | 'awaiting_requirement_confirmation'
  | 'generating_project_plan'
  | 'awaiting_project_plan_confirmation'
  | 'generating_application_template_files'
  | 'application_template_generation_failed'
  | 'ready_for_workbench'

export type ApplicationLifecycle = {
  schemaVersion: '1.0.0'
  application: { id: string; name: string }
  project?: { id: string }
  updatedAt: string
  revision: number
  lifecycle: {
    stage: ApplicationLifecycleStage
    status: 'pending' | 'running' | 'awaiting_user' | 'failed' | 'completed' | 'cancelled'
    domain: Record<string, unknown>
    extensions: Record<string, unknown>
  }
  activeThreadId?: string
  activeRunId?: string
  pendingInteraction?: {
    id: string
    type: string
    basedOnRevision: number
    payload: Record<string, unknown>
    artifactRefs: Array<Record<string, unknown>>
    createdAt: string
    submittedAt?: string
  }
  error?: { code: string; message: string; recoverable: boolean }
  recovery?: Record<string, unknown>
  extensions: Record<string, unknown>
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
  buildExecutionScope?: WorkflowBuildExecutionScope
  requirementSpecPath?: string
  projectPlanPath?: string
  workspaceSnapshotPath?: string
  buildTaskPlanPath?: string
}

export type WorkflowBuildExecutionScope = {
  type: 'application' | 'page' | 'data_source'
  targetId?: string
}

export type WorkflowBuildToolActivity = {
  callId: string
  tool: 'ls' | 'read_file' | 'glob' | 'grep' | 'write_file' | 'edit_file' | 'delete_file'
  category: 'browse' | 'read' | 'search' | 'write' | 'delete'
  status: 'running' | 'failed'
  message: string
  path?: string
}

export type WorkflowBuildExecutionTask = {
  id?: string
  task_id?: string
  unit_id?: string
  owner?: string
  title?: string
  description?: string
  status?: 'pending' | 'running' | 'completed' | 'failed' | string
  dependencies?: string[]
  dependsOn?: string[]
  targetFiles?: string[]
  target_files?: string[]
  allowedPaths?: string[]
  allowed_paths?: string[]
  acceptanceCriteria?: string[]
  acceptance_criteria?: string[]
  source_refs?: Record<string, unknown>
  failure_category?: string | null
  failure_reason?: string | null
  failure_detail?: Record<string, unknown> | null
  activeToolActivity?: WorkflowBuildToolActivity
}

export type WorkflowBuildExecutionSlice = {
  scope?: WorkflowBuildExecutionScope
  target_unit_ids?: string[]
  unit_ids?: string[]
  task_ids?: string[]
  pending_task_ids?: string[]
  reusable_task_ids?: string[]
  tasks?: WorkflowBuildExecutionTask[]
  summary?: {
    total?: number
    pending?: number
    running?: number
    reused?: number
    completed?: number
    failed?: number
  }
}
