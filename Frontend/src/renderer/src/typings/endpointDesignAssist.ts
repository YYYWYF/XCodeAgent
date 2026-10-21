import type { BindingSelection } from './endpointDesign'
import type { WorkflowApiDatabaseOperation, WorkflowApiDesignDraft } from './workflow'

/** 当前单表编辑器可请求的辅助区域。 */
export type MappingAssistArea = 'query' | 'write' | 'return' | 'description'
export type MappingAssistStep = 'operation' | MappingAssistArea
export type MappingAssistAction = 'generate_mapping' | 'refine_rule' | 'replace_mapping'
export type MappingAssistBaseline = { draftHash: string; metadataHash: string }
export type GeneratedMapping = Pick<WorkflowApiDesignDraft, 'databaseWrites' | 'fieldMappings' | 'implementationDescription'> & {
  databaseOperation: WorkflowApiDatabaseOperation
  databaseQuery: WorkflowApiDesignDraft['databaseQuery'] | null
}
export type MappingAssistance = {
  baseline: MappingAssistBaseline; mapping: GeneratedMapping | null; questions: string[]
  operation: WorkflowApiDatabaseOperation | null; operationReason: string
  reviews: Array<{ area: MappingAssistArea; key: string; reason: string; needsReview: boolean }>
}
export type MappingAssistFailure = { retryToken: string; failedStage: MappingAssistStep; completedStages: MappingAssistStep[] }
export type MappingApplied = {
  draft: WorkflowApiDesignDraft; areas: MappingAssistArea[]; targets: Array<{ area: MappingAssistArea; key: string }>
}
export type MappingAssistProgress = { stage: string; message: string; percent: number }
export type MappingAssistRequest = {
  workspaceRoot: string; apiContractId: string; endpointId: string; draft: WorkflowApiDesignDraft
  selection: Extract<BindingSelection, { sourceType: 'database' }>
  baseRevision: string | null; technicalPlanHash: string
}
/** 首次需求必填，后续字段回答与其他补充单独传递。 */
export type MappingGenerationInput = { instruction: string; clarification: string; retryToken?: string }
export type RuleAssistTarget = { area: 'query' | 'write' | 'return'; key: string }
export type RuleAssistDependency = { kind: 'endpoint' | 'source' | 'builtin'; field: Record<string, unknown> }
export type RefinedMappingRule = { description: string; questions: string[] }
