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
  launchProgress?: WorkflowLaunchProgress
  acceptanceRequest?: WorkflowAcceptanceRequest
  artifacts?: Record<string, string>
  clarification?: WorkflowClarification
  requirementsConfirmed?: boolean
  smallTaskTasks?: WorkflowSmallTask[]
  smallTaskResults?: WorkflowSmallTaskResult[]
  smallTaskHandoff?: WorkflowSmallTaskHandoff
  buildSummary?: WorkflowBuildSummary
  buildTaskPlan?: WorkflowBuildTaskPlan
  buildExecutionScope?: WorkflowBuildExecutionScope
  lastPersistedBuildExecutionScope?: WorkflowBuildExecutionScope
  buildTaskPlanPersisted?: boolean
  buildTaskPlanConfirmation?: WorkflowClarification
  testTarget?: WorkflowTestTarget
  unitTestSummary?: Record<string, unknown>
  unitTestResults?: Array<Record<string, unknown>>
  unitTestReport?: Record<string, unknown>
  unitTestGeneration?: Record<string, unknown>
  unitTestGenerationContext?: Record<string, unknown>
  reviewPhaseConfirmation?: WorkflowClarification
  acceptancePhaseConfirmation?: WorkflowClarification
  testReportResult?: WorkflowTestReportResult
  codeReviewResult?: WorkflowCodeReviewResult
  codeReviewRepair?: WorkflowCodeReviewRepair
  codeReviewRetry?: WorkflowCodeReviewRetry
  unitTestQualityGatePassed?: boolean
  unitTestGatePassed?: boolean
  unitTestNextAction?: string
  unitTestDecision?: 'run' | 'skip' | string
  unitTestBuildCodeChanges?: WorkspaceCodeChangeSet
  unitTestBuildDiffCaptured?: boolean
  unitTestRepairTaskPlan?: Record<string, unknown>
  unitTestRepairIteration?: number
  unitTestMaxRepairIterations?: number
  repairReturnNode?: 'unit_test' | 'integration_test' | string
  lifecycle?: ApplicationLifecycle
  revisionImpact?: WorkflowRevisionImpact
  revisionContinuation?: WorkflowRevisionContinuation
  revisionDraft?: WorkflowRevisionDraft
  [key: string]: unknown
}

export type WorkflowFormalRevisionBranch =
  | 'design_stage_revision'
  | 'workbench_plan_revision'

export type WorkflowRevisionImpact = {
  interactionId: string
  formalBranch: WorkflowFormalRevisionBranch
  revisionType:
    | 'requirement_scope_change'
    | 'product_behavior_change'
    | 'ui_visual_change'
    | 'technical_contract_change'
    | 'endpoint_implementation_change'
    | 'data_source_change'
  earliestArtifact:
    | 'requirement-spec'
    | 'product-plan'
    | 'ui-design'
    | 'technical-plan'
  affectedArtifacts: string[]
  affectedResources: string[]
  reason: string
  risks: string[]
  /** 只读确认 JSON 的逐条定位证据；不包含 Markdown 文档内容。 */
  evidence: WorkflowContractEvidence[]
  analysisStatus?: 'completed' | 'insufficient_evidence' | string
  status: 'pending' | 'approved' | 'rejected' | 'expired'
}

export type WorkflowContractEvidence = {
  artifactKey: string
  jsonPointer: string
  selector?: Record<string, string>
  artifactSha256?: string
  contractStage: 'requirement_design' | 'planning_design' | string
  existingFact: string
  requestedChange: string
  conflictRelation:
    | 'contradicts'
    | 'removes'
    | 'reassigns'
    | 'modifies'
    | 'preserves'
    | string
  reason: string
}

export type WorkflowRevisionContinuation = {
  changeId: string
  formalBranch: WorkflowFormalRevisionBranch
  action: 'continue_revision_build'
  token: string
  technicalPlanSha256: string
}

export type WorkflowRevisionDraft = {
  artifactKey: string
  markdown: string
  draftSha256: string
  basedOn: Array<{ artifactKey: string; sha256: string }>
  status: 'pending_user_confirmation'
}

export type WorkflowRevisionDraftInteraction = {
  changeId: string
  interactionId: string
  basedOnLifecycleRevision: number
  artifactKey: string
  draftSha256: string
  action: 'confirm' | 'save' | 'revise' | 'discard'
  feedback?: string
  editedMarkdown?: string
}

/** 项目启动子图通过 AG-UI 实时投影的当前子步骤。 */
export type WorkflowLaunchProgress = {
  stage?: 'structure' | 'backend' | 'frontend' | 'ready' | string
  status?: 'pending' | 'running' | 'completed' | 'skipped' | 'failed' | string
  message?: string
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

export type WorkflowLaunchPart = {
  status?: string
  message?: string
  preview_url?: string
  reason?: string
  [key: string]: unknown
}

export type WorkflowLaunchResult = {
  status?: string
  message?: string
  preview_url?: string
  server?: Record<string, unknown>
  backend?: WorkflowLaunchPart
  frontend?: WorkflowLaunchPart
  failed_stage?: string
  [key: string]: unknown
}

export type WorkflowAcceptanceRequest = {
  status?: string
  message?: string
  preview_url?: string
  server?: Record<string, unknown>
  [key: string]: unknown
}

/** 前后端代码审查节点返回的只读扫描结果。 */
export type WorkflowCodeReviewResult = {
  status?: 'completed' | string
  summary?: string
  reportPath?: string
  issueCount?: number
  truncated?: boolean
  loadedSkills?: string[]
  targets?: Array<{
    side?: 'frontend' | 'backend' | string
    root?: string
    status?: 'completed' | 'skipped' | string
    scannedFileCount?: number
    warning?: string
  }>
  issues?: Array<{
    id?: string
    side?: 'frontend' | 'backend' | string
    ruleId?: string
    severity?: 'critical' | 'high' | 'medium' | 'low' | string
    title?: string
    summary?: string
    file?: string
    line?: number
  }>
}

/** 测试阶段生成的用户可读 Markdown 报告。 */
export type WorkflowTestReportResult = {
  reportPath?: string
}

/** 代码审查一键修复及独立构建检查状态。 */
export type WorkflowCodeReviewRepair = {
  status?:
    | 'not_required'
    | 'awaiting_user'
    | 'repairing'
    | 'building'
    | 'completed'
    | 'failed'
    | string
  iteration?: number
  maxIterations?: number
  requestedIssueCount?: number
  attemptedIssueIds?: string[]
  summary?: string
  changedFiles?: string[]
  buildChecks?: Array<{
    id?: string
    name?: string
    layer?: string
    status?: 'running' | 'passed' | 'skipped' | 'failed' | string
    evidence?: string
  }>
  failure?: string
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
  target_type: 'page' | 'endpoint' | 'entity'
  target_id: string
  name?: string
  entity_id?: string
  description?: string
  module_id?: string
  data_source_type?: string
  design_stage?: string
  fields?: Array<Record<string, unknown>>
  default_constraints?: Array<Record<string, unknown>>
  table_design?: Record<string, unknown>
  database_design?: Record<string, unknown>
  external_api_design?: WorkflowExternalApiDesign
  related_endpoints?: WorkflowExternalApiEndpointRef[]
  static_design?: Record<string, unknown>
  database_execution?: Record<string, unknown>
  table_operations_executed?: boolean
  business_rules?: Array<Record<string, unknown>>
  relationships?: Array<Record<string, unknown>>
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
  endpoint_decision?: Record<string, unknown>
  interface_design?: Record<string, unknown>
  processing_logic?: string[]
  risks?: string[]
}

export type WorkflowEntityDesignOption = {
  value: 'database' | 'external_api' | 'static'
  label: string
  available?: boolean
  description?: string
}

export type WorkflowEntityDesignSummary = {
  stage?: string
  entity_id?: string
  entity_name?: string
  entity_description?: string
  field_count?: number
  default_constraints?: Array<Record<string, unknown>>
  fields?: Array<{
    name: string
    label?: string
    type?: string
    required?: boolean
  }>
  data_source_type?: string
  database_context_ready?: boolean
  data_source_options?: WorkflowEntityDesignOption[]
  ai_suggestions?: {
    assist_type?: string
    operation_id?: string
    text?: string
    messages?: Array<{
      role?: string
      content?: string
    }>
    missing_fields?: {
      table_name?: string
      eligible?: boolean
      fields?: Array<{
        entity_field?: string
        label?: string
        type?: string
        nullable?: boolean
        comment?: string
      }>
    }
    suggestions?: WorkflowEntityDesignSuggestion[]
    source?: string
    note?: string
  }
  database_design?: {
    binding_status?: string
    matched_table?: string | null
    table_count?: number
    binding_count?: number
    difference_count?: number
    operation_count?: number
    table_generation_required?: boolean
    table_generation_approved?: boolean
  }
  external_api_design?: {
    base_url?: string
    base_url_config_key?: string
    timeout_ms?: number
    shared_header_count?: number
    operation_count?: number
    endpoint_binding_count?: number
    operations?: Array<Record<string, unknown>>
  }
  related_endpoints?: WorkflowExternalApiEndpointRef[]
  ddl_execution?: {
    status?: 'completed' | 'failed' | 'already_satisfied'
    table_name?: string
    columns?: string[]
    message?: string
    execution?: Record<string, unknown>
  }
  static_design?: {
    seed_row_count?: number
    field_value_count?: number
  }
  database_execution?: {
    status?: string
    summary?: string
    operation_count?: number
  }
  validation_errors?: string[]
}

export type WorkflowEntityDesignSuggestion = {
  id?: string
  label?: string
  value?: string
  payload?: Record<string, unknown>
  source?: string
  note?: string
}

export type WorkflowDetailReview = {
  pages?: WorkflowDetailReviewTarget[]
  endpoints?: WorkflowDetailReviewTarget[]
  entities?: WorkflowDetailReviewTarget[]
  summary?: {
    page_count?: number
    endpoint_count?: number
    entity_count?: number
    api_contract_count?: number
    missingSelectedPagePlan?: boolean
    missingSelectedEndpointPlan?: boolean
    missingSelectedEntityPlan?: boolean
    selectedPageId?: string
    selectedApiContractId?: string
    selectedEndpointId?: string
    selectedEntityId?: string
    detailTargetType?: 'page' | 'endpoint' | 'entity'
    entityDesign?: WorkflowEntityDesignSummary
  }
}

export type WorkflowEntityDesignAction = {
  action:
    | 'select_data_source'
    | 'submit_static_data'
    | 'submit_bindings'
    | 'approve_table_generation'
    | 'list_tables'
    | 'select_table'
    | 'ai_assist'
    | 'execute_add_columns'
    | 'execute_create_table'
    | 'submit_entity_design'
  entity_id: string
  data_source_type?: 'database' | 'external_api' | 'static'
  table_name?: string
  matched_table?: string
  assist_type?: string
  operation_id?: string
  instruction?: string
  context?: Record<string, unknown>
  fields?: Array<Record<string, unknown>>
  proposal?: Record<string, unknown>
  database_design?: Record<string, unknown>
  external_api_design?: WorkflowExternalApiDesign
  static_design?: Record<string, unknown>
  business_rules?: Array<Record<string, unknown>>
  relationships?: Array<Record<string, unknown>>
  acceptance_criteria?: string[]
  risks?: string[]
  seed_rows?: Array<Record<string, unknown>>
  field_values?: Record<string, string[]>
  bindings?: Array<Record<string, unknown>>
}

/** 外部 API 请求参数的当前公共契约。 */
export type WorkflowExternalApiParameter = {
  name: string
  in: 'path' | 'query'
  type: 'string' | 'number' | 'boolean'
  required: boolean
  example?: unknown
}

/** 外部 API 响应分页语义的当前公共契约。 */
export type WorkflowExternalApiPagination = {
  page_parameter: string
  size_parameter: string
  page_index_base: 0 | 1
}

/** 外部 API 操作与本系统 Endpoint 的稳定关联。 */
export type WorkflowExternalApiEndpointRef = {
  api_contract_id: string
  endpoint_id: string
  method?: string
  path?: string
  summary?: string
}

/** 外部 API 绑定在 AG-UI 动作中的可执行契约。 */
export type WorkflowExternalApiDesign = {
  connection: {
    base_url: string
    base_url_config_key: string
    timeout_ms: number
    headers: Array<{ name: string; value: string }>
  }
  operations: Array<{
    operation_id: string
    name: string
    endpoint_refs: WorkflowExternalApiEndpointRef[]
    connection_override?: {
      base_url?: string
      base_url_config_key?: string
      timeout_ms?: number
    }
    api_info: {
      method: string
      path: string
      parameters: WorkflowExternalApiParameter[]
      headers: Array<{ name: string; value: string }>
      request_body: unknown
      response_body: unknown
    }
    response_handling: {
      entity_payload: boolean
      cardinality: 'object' | 'array' | 'page'
      payload_path: string
      success_status_codes: number[]
      error_message_path?: string
      total_path?: string
      pagination?: WorkflowExternalApiPagination
    }
    field_mappings: Array<{
      entity_field: string
      source_field: string
      rule: 'same_name' | 'nested_match' | 'ai' | 'manual'
    }>
  }>
}

export type WorkflowDetailReviewSubmission = {
  review_status: 'confirmed'
  target_changes: Array<{
    target_type: 'page' | 'endpoint' | 'entity'
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
  | WorkflowEntityDesignAction
  | WorkflowRequirementSpecEdit
  | WorkflowBuildTaskPlanConfirmation
  | Record<string, unknown>

export type ApplicationPlanningAction =
  | 'answer'
  | 'confirm'
  | 'revise'
  | 'ui_action'
  | 'enter_planning'
  | 'design_change'

export type WorkflowClarificationAnswers = Record<string, WorkflowClarificationAnswer> & {
  /** 由创建规划 UI 明确写入，不能由确认文案反推。 */
  __applicationPlanningAction?: ApplicationPlanningAction
  /** 单元测试门禁的结构化运行或跳过选择。 */
  unit_test_confirmation?: 'run' | 'skip'
  /** Build 完成后进入测试阶段的唯一结构化确认动作。 */
  test_phase_confirmation?: WorkflowTestPhaseConfirmation
  /** 集成测试通过后进入审查阶段的唯一结构化确认动作。 */
  review_phase_confirmation?: WorkflowReviewPhaseConfirmation
  /** 代码审查完成后进入验收阶段的唯一结构化确认动作。 */
  acceptance_phase_confirmation?: WorkflowAcceptancePhaseConfirmation
  /** 代码审查问题的一键修复动作。 */
  code_review_repair_confirmation?: WorkflowCodeReviewRepairConfirmation
  /** 正式修改影响范围的结构化批准或拒绝动作。 */
  revision_impact_confirmation?: 'approved' | 'rejected'
  /** 前后端实现修复开始前的用户确认。 */
  implementation_fix_confirmation?: 'approved' | 'rejected'
  /** 正式草稿交互必须携带服务端绑定的 change、revision 与 hash。 */
  revision_draft_interaction?: WorkflowRevisionDraftInteraction
}

/** 开发完成后恢复测试阶段确认节点的协议答案。 */
export type WorkflowTestPhaseConfirmation = {
  action: 'confirm'
}

/** 集成测试通过后恢复审查阶段确认节点的协议答案。 */
export type WorkflowReviewPhaseConfirmation = {
  action: 'confirm'
}

/** 代码审查完成后恢复验收阶段确认节点的协议答案。 */
export type WorkflowAcceptancePhaseConfirmation = {
  action: 'confirm'
}

/** 审查阶段一键修复只能提交 repair_all。 */
export type WorkflowCodeReviewRepairConfirmation = {
  action: 'repair_all'
}

export type WorkflowBuildTaskPlanPatch = {
  task_id: string
  title?: string
  description?: string
}

export type WorkflowBuildTaskPlanConfirmation = {
  mode?: 'build_task_plan_confirmation' | string
  action: 'confirm' | 'patch' | 'regenerate'
  patches?: WorkflowBuildTaskPlanPatch[]
}

export type ApplicationPlanningInteraction = {
  gateId: string
  artifact: 'requirement_spec' | 'product_plan' | 'ui_designs' | 'technical_plan'
  artifactRevision: string
  action: ApplicationPlanningAction
  request?: string
  answers?: WorkflowClarificationAnswers
  editedRequirementSpec?: Record<string, unknown>
  requirementSpecFeedback?: string
  uiAction?: Record<string, unknown>
}

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
  missing_entities?: Array<{
    entity_id?: string
    entity_name?: string
  }>
  taskPlan?: WorkflowBuildTaskPlan
  buildExecutionScope?: WorkflowBuildExecutionScope
  testTarget?: WorkflowTestTarget
  confirmationStatus?: 'pending' | 'confirmed' | string
  editableFields?: string[]
  actionValues?: string[]
  errors?: string[]
  [key: string]: unknown
}

/** 开发完成确认后展示给用户的测试目标摘要。 */
export type WorkflowTestTarget = {
  type: 'page' | 'endpoint' | 'data_source' | 'application' | string
  id: string
  label: string
}

export type WorkflowBuildTaskPlan = {
  version?: string
  schemaVersion?: string
  status?: 'ready' | 'blocked' | string
  confirmationStatus?: 'pending' | 'confirmed' | string
  summary?: Record<string, unknown>
  tasks?: WorkflowBuildTaskPlanTask[]
  taskGraph?: {
    edges?: Array<Record<string, unknown>>
  }
}

export type WorkflowBuildTaskPlanTask = {
  id: string
  title: string
  description: string
  owner?: string
  unit_id?: string
  dependencies?: string[]
  target_files?: string[]
  allowed_paths?: string[]
  change_scope?: Array<Record<string, unknown>>
  deliverables?: Array<Record<string, unknown>>
  acceptance_checks?: Array<Record<string, unknown>>
  business_acceptance_checks?: Array<Record<string, unknown>>
  business_acceptance_evidence?: Array<Record<string, unknown>>
  business_acceptance_summary?: Record<string, unknown>
  status?: string
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
  engineeringAcceptanceChecks?: Array<Record<string, unknown>>
  businessAcceptanceChecks?: Array<Record<string, unknown>>
  businessAcceptanceSummary?: Record<string, unknown>
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
  mode?: 'small_task_scope_confirmation' | string
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
  | 'generating_requirement_document'
  | 'awaiting_requirement_document_confirmation'
  | 'generating_ui_designs'
  | 'awaiting_ui_design_confirmation'
  | 'awaiting_planning_stage_entry'
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
  repositoryUrl?: string
  branch?: 'main' | 'auth'
  commitSha?: string
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
  type: LifecyclePendingInteractionType
  basedOnRevision: number
  payload: Record<string, unknown>
  artifactRefs: Array<Record<string, unknown>>
  createdAt: string
  submittedAt?: string | null
}

/** 当前工作台 execution 支持跨会话恢复的结构化交互类型。 */
export type LifecyclePendingInteractionType =
  | 'requirement_clarification'
  | 'requirement_document_confirmation'
  | 'technical_plan_confirmation'
  | 'page_design_confirmation'
  | 'task_plan_confirmation'
  | 'impact_confirmation'
  | 'page_acceptance'
  | 'application_acceptance'
  | 'agent_approval'
  | 'repair_scope_confirmation'
  | 'unit_test_confirmation'
  | 'frontend_performance_confirmation'
  | 'test_phase_confirmation'
  | 'review_phase_confirmation'
  | 'code_review_repair_confirmation'
  | 'acceptance_phase_confirmation'
  | 'revision_draft_confirmation'
  | 'plan_adjustment'

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
  pendingRevisionImpact?: Record<string, unknown>
  activeFormalRevision?: {
    changeId: string
    formalBranch: WorkflowFormalRevisionBranch
    impactInteractionId: string
    sourceThreadId: string
    sourceRunId: string
    planningThreadId: string
    status: string
    currentArtifact?: string | null
    remainingArtifacts?: string[]
    continuationSourceRunId?: string
    [key: string]: unknown
  }
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

export type WorkflowAction =
  | 'retry_failed_tasks'
  | 'retry_code_review'
  | 'start_design_revision'
  | 'start_technical_revision'
  | 'start_revision'
  | 'submit_revision_interaction'
  | 'continue_revision_build'

export type WorkflowCodeReviewRetry = {
  available: true
  target: 'scan' | 'repair'
}

export type WorkflowDesignStageRevisionStart = {
  request: string
  target: Record<string, unknown>
  impact: WorkflowRevisionImpact
  sourceSessionId: string
  sourceConversationThreadId: string
  sourceRunId: string
}

/** TechnicalPlan-only 正式修订进入独立规划会话时使用的前端交接合同。 */
export type WorkflowWorkbenchPlanRevisionStart = WorkflowDesignStageRevisionStart

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
  engineeringAcceptanceChecks?: Array<Record<string, unknown>>
  engineering_acceptance_checks?: Array<Record<string, unknown>>
  acceptanceEvidence?: Array<Record<string, unknown>>
  acceptance_evidence?: Array<Record<string, unknown>>
  businessAcceptanceChecks?: Array<Record<string, unknown>>
  business_acceptance_checks?: Array<Record<string, unknown>>
  businessAcceptanceEvidence?: Array<Record<string, unknown>>
  business_acceptance_evidence?: Array<Record<string, unknown>>
  businessAcceptanceSummary?: Record<string, unknown>
  business_acceptance_summary?: Record<string, unknown>
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
