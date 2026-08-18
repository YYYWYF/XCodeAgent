import type {
  ApplicationDevelopmentTask,
  ApplicationMenuItem,
  ApplicationSharedModule
} from './application'

export type DevelopmentPlanningQuestion = {
  id: string
  question: string
  rationale: string
  placeholder: string
}
export type DevelopmentPlanningAnswer = { questionId: string; question: string; answer: string }
export type DevelopmentPlanningPageOption = {
  pageId: string
  key: string
  label: string
  path: string
  purpose: string
  detailPlanStatus?: string
  hasDetailPlan?: boolean
  designed: boolean
  taskSummary?: {
    total: number
    pending: number
    running: number
    completed: number
    failed: number
  }
}
export type DevelopmentPlanningPageTreeNode = {
  key: string
  type: 'menu' | 'page'
  label: string
  uniquePath?: string
  pageId?: string
  path?: string
  purpose?: string
  detailPlanStatus?: string
  hasDetailPlan?: boolean
  designed?: boolean
  children?: DevelopmentPlanningPageTreeNode[]
}
export type DevelopmentPlanningApiEndpoint = {
  apiContractId?: string
  id: string
  method: string
  path: string
  summary: string
  detailPlanStatus?: string
  hasDetailPlan?: boolean
  designed?: boolean
}
export type DevelopmentPlanningApiContract = {
  id: string
  label: string
  dataSourceIds?: string[]
  endpoints: DevelopmentPlanningApiEndpoint[]
}
export type DevelopmentPlanningEntityOption = {
  id: string
  label: string
  purpose: string
  dataSourceType: string
  fields?: Array<{
    name: string
    label?: string
    type: string
    required?: boolean
  }>
  detail?: {
    entity_id?: string
    entity_name?: string
    description?: string
    design_stage?: string
    data_source_type?: string
    status?: string
    fields?: Array<{
      name?: string
      label?: string
      type?: string
      required?: boolean
      column_type?: string
    }>
    table_design?: {
      name?: string
      columns?: Array<{
        name?: string
        type?: string
        comment?: string
        nullable?: boolean
      }>
    }
    database_design?: {
      database_name?: string
      matched_table?: string | null
      bindings?: Array<Record<string, unknown>>
      table_generation?: Record<string, unknown>
      selected_table?: {
        name?: string
        comment?: string
        columns?: Array<{
          name?: string
          type?: string
          nullable?: boolean
          comment?: string
        }>
      }
      schema_context?: {
        database?: string
        tables?: Array<{
          name?: string
          table_name?: string
          comment?: string
          columns?: Array<{
            name?: string
            type?: string
            nullable?: boolean
            comment?: string
          }>
        }>
      }
    }
    external_api_design?: Record<string, unknown>
    static_design?: Record<string, unknown>
    business_rules?: Array<Record<string, unknown>>
    relationships?: Array<Record<string, unknown>>
    acceptance_criteria?: unknown
    risks?: unknown
  }
  detailPlanStatus?: string
  hasDetailPlan: boolean
  designed: boolean
}
export type MenuDevelopmentPlan = {
  menuKey: string
  menuLabel: string
  tasks: ApplicationDevelopmentTask[]
}

export type ApplicationDevelopmentPlan = {
  schemaVersion: 1
  summary: string
  executionOrder: string[]
  sharedModules: ApplicationSharedModule[]
  menuPlans: MenuDevelopmentPlan[]
}

export type DevelopmentPlanningProgress = {
  stage: string
  message: string
  detail: string
  percent: number
}

export type ConfirmedDevelopmentPlan = {
  path: string
  sha256: string
  confirmedAt: string
  menus: {
    homeMenuKey: string
    items: ApplicationMenuItem[]
    sharedModules: ApplicationSharedModule[]
    developmentPlan: { schemaVersion: 1; summary: string; executionOrder: string[] }
  }
}
