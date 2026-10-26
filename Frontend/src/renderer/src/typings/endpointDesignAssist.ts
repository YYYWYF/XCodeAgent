import type { BindingSelection } from './endpointDesign'
import type { WorkflowApiDatabaseOperation, WorkflowApiDesignDraft, WorkflowApiSourceField } from './workflow'

/** 当前数据库、外部 API 和静态编辑器的受限辅助区域。 */
export type MappingAssistArea = 'query' | 'write' | 'request' | 'return' | 'description'
export type MappingAssistStep = 'operation' | MappingAssistArea
export type MappingAssistAction = 'generate_mapping' | 'refine_rule' | 'replace_mapping' | 'generate_mock_data' | 'apply_mock_data'
export type MappingAssistBaseline = { draftHash: string; metadataHash: string }
export type GeneratedMapping = Pick<WorkflowApiDesignDraft, 'fieldMappings'> & ({
  databaseOperation: WorkflowApiDatabaseOperation
  databaseQuery: WorkflowApiDesignDraft['databaseQuery'] | null
  databaseWrites: WorkflowApiDesignDraft['databaseWrites']
  externalApiBindings?: never
  staticData?: never; implementationDescription?: string
} | {
  externalApiBindings: NonNullable<WorkflowApiDesignDraft['externalApiBindings']>
  databaseOperation?: never; databaseQuery?: never; databaseWrites?: never
  staticData?: never; implementationDescription?: string
} | {
  staticData: NonNullable<WorkflowApiDesignDraft['staticData']>
  databaseOperation?: never; databaseQuery?: never; databaseWrites?: never
  externalApiBindings?: never; implementationDescription?: never
})
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
  selection: BindingSelection
  baseRevision: string | null; technicalPlanHash: string
}
/** 首次需求必填，后续字段回答与其他补充单独传递。 */
export type MappingGenerationInput = { instruction: string; clarification: string; retryToken?: string }
export type RuleAssistTarget = { area: 'query' | 'write' | 'request' | 'return'; key: string }
export type RuleAssistDependency = { kind: 'endpoint' | 'source' | 'builtin'; field: Record<string, unknown> }
export type RefinedMappingRule = { description: string; questions: string[] }

/** Mock 候选只含样例和校验信息，采用前不修改静态内容或返回映射。 */
export type MockDataAssistance = {
  baseline: MappingAssistBaseline
  data: NonNullable<WorkflowApiDesignDraft['staticData']> | null
  fields: WorkflowApiSourceField[]
  questions: string[]; warnings: string[]
}
