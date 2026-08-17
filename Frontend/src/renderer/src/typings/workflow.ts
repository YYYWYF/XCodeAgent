import type { WorkspaceCodeChangeSet } from './codeChanges'
import type { ToolApproval } from '../service/workspaceTools'

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
  acceptanceAdjustment?: WorkflowAcceptanceAdjustment
  smallTaskTasks?: WorkflowSmallTask[]
  smallTaskResults?: WorkflowSmallTaskResult[]
  smallTaskHandoff?: WorkflowSmallTaskHandoff
  buildSummary?: WorkflowBuildSummary
  lifecycle?: ApplicationLifecycle
  [key: string]: unknown
}

export type WorkflowBuildSummary = {
  status?: string
  retryable_failures?: number
  retryable_task_ids?: string[]
  retry_available?: boolean
  recovery_available?: boolean
  recovery_task_ids?: string[]
  recovery_mode?: 'retry' | 'repair' | string
  retry_requested?: boolean
  retry_message?: string
  repairable_failures?: number
  requires_confirmation?: number
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

export type WorkflowAcceptanceAdjustmentType =
  | 'local_fix'
  | 'page_design_change'
  | 'endpoint_change'
  | 'data_source_change'
  | 'project_plan_change'

export type WorkflowAcceptanceAdjustment = {
  type: WorkflowAcceptanceAdjustmentType
  feedback: string
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
  target_type: 'page' | 'endpoint'
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
  response_bindings?: Array<Record<string, unknown>>
  acceptance_criteria?: string[]
  dependent_pages?: Array<Record<string, unknown>>
  api_contract_id?: string
  endpoint_id?: string
  data_source_id?: string
  method?: string
  summary?: string
  data_usage?: Record<string, unknown>
  data_origin?: Record<string, unknown>
  endpoint_decision?: Record<string, unknown>
  interface_design?: Record<string, unknown>
  processing_logic?: string[]
  risks?: string[]
}

export type WorkflowDetailReview = {
  pages?: WorkflowDetailReviewTarget[]
  endpoints?: WorkflowDetailReviewTarget[]
  summary?: {
    page_count?: number
    endpoint_count?: number
    api_contract_count?: number
    missingSelectedPagePlan?: boolean
    missingSelectedEndpointPlan?: boolean
    selectedPageId?: string
    selectedApiContractId?: string
    selectedEndpointId?: string
    detailTargetType?: 'page' | 'endpoint'
  }
}

export type WorkflowDetailReviewSubmission = {
  review_status: 'confirmed'
  target_changes: Array<{
    target_type: 'page' | 'endpoint'
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
  | WorkflowAcceptanceAdjustment
  | WorkflowRequirementSpecEdit
  | Record<string, unknown>

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
  approval?: ToolApproval
  database_change_plan?: {
    summary?: string
    statements?: string[]
  }
  [key: string]: unknown
}

export type WorkflowSmallTask = {
  id?: string
  taskId?: string
  owner?: string
  title?: string
  description?: string
  status?: string
  allowedPaths?: string[]
  targetFiles?: string[]
  acceptanceCriteria?: string[]
  dependencies?: string[]
  [key: string]: unknown
}

export type WorkflowSmallTaskResult = {
  taskId?: string
  owner?: string
  status?: string
  summary?: string
  changedFiles?: string[]
  verification?: string[]
  failureReason?: string | null
  escalation?: Record<string, unknown>
  [key: string]: unknown
}

export type WorkflowSmallTaskHandoff = {
  mode?: 'small_task_scope_confirmation' | 'small_task_workflow_handoff' | string
  status?: string
  reason?: string
  requestedPaths?: string[]
  requestedResources?: Array<Record<string, unknown>>
  workflowIntent?: string
  taskIds?: string[]
  [key: string]: unknown
}

export type WorkflowConfirmationArtifact = {
  id: 'requirement_spec' | 'product_plan' | 'technical_plan' | 'project_plan'
  name: string
  path: string
  format: 'markdown'
  content: string
}

export type WorkflowProjectPlanUpdateSection = {
  id: string
  kind: 'page' | 'endpoint'
  title: string
  subtitle?: string
  content: string
}

export type WorkflowProjectPlanUpdate = {
  format: 'markdown'
  readOnly: true
  documentName: string
  status: 'confirmed'
  targetType: 'page' | 'endpoint'
  targetId: string
  summary: {
    pageCount: number
    endpointCount: number
  }
  sections: WorkflowProjectPlanUpdateSection[]
}

export type ApplicationLifecycleStage =
  | 'collecting_requirement'
  | 'analyzing_requirement'
  | 'awaiting_requirement_clarification'
  | 'generating_requirement_spec'
  | 'awaiting_requirement_confirmation'
  | 'generating_product_plan'
  | 'awaiting_product_plan_confirmation'
  | 'generating_ui_designs'
  | 'awaiting_ui_design_confirmation'
  | 'generating_technical_plan'
  | 'awaiting_technical_plan_confirmation'
  | 'generating_application_template_files'
  | 'application_template_generation_failed'
  | 'ready_for_workbench'

export type TemplateDownloadTargetResult = {
  status: 'succeeded' | 'failed' | 'pending'
  attempt: number
  path: string
  error?: string
}

export type TemplateDownloadResult = {
  ok: boolean
  status: 'succeeded' | 'failed'
  failedTargets: Array<'frontend' | 'backend'>
  targets: {
    frontend: TemplateDownloadTargetResult
    backend: TemplateDownloadTargetResult
  }
}

export type WorkbenchExecutionStatus =
  | 'running'
  | 'stopping'
  | 'awaiting_user'
  | 'failed'
  | 'stopped'
  | 'completed'

export type LifecyclePendingInteraction = {
  id: string
  type: string
  basedOnRevision: number
  payload: Record<string, unknown>
  artifactRefs: Array<Record<string, unknown>>
  createdAt: string
  submittedAt?: string | null
}

export type LifecycleError = {
  code: string
  message: string
  recoverable: boolean
}

export type WorkbenchExecution = {
  scope: 'application' | 'page' | 'data_source' | 'endpoint'
  targetId: string
  pageId?: string
  threadId: string
  runId: string
  phase: string
  status: WorkbenchExecutionStatus
  resourceKeys?: string[]
  pendingInteraction?: LifecyclePendingInteraction
  error?: LifecycleError
  startedAt: string
  updatedAt: string
}

export type ExecutionResourceLock = {
  runId: string
  ownerPageId?: string
  mode: 'exclusive'
  role: 'primary' | 'dependency'
  reason: 'primary_target' | 'plan_dependency' | 'repair_expansion'
  acquiredAt: string
}

export type ApplicationLifecycle = {
  schemaVersion: '1.2.0'
  application: { id: string; name: string }
  updatedAt: string
  revision: number
  initialization: {
    stage: ApplicationLifecycleStage
    threadId?: string
    status:
      | 'pending'
      | 'running'
      | 'awaiting_user'
      | 'failed'
      | 'completed'
      | 'cancelled'
      | 'stopping'
      | 'stopped'
  }
  activeRunId?: string
  activeExecutions: Record<string, WorkbenchExecution>
  resourceLocks?: {
    application?: ExecutionResourceLock
    pages: Record<string, ExecutionResourceLock>
    endpoints?: Record<string, ExecutionResourceLock>
    apiContracts: Record<string, ExecutionResourceLock>
    dataSources: Record<string, ExecutionResourceLock>
  }
  error?: LifecycleError
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

export type WorkflowAction = 'retry_failed_tasks'

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
  type: 'application' | 'page' | 'data_source' | 'endpoint'
  targetId?: string
  apiContractId?: string
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
