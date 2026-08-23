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

/** 开发阶段的实体占位产物；本轮只展示类型、状态和直接依赖，不进入实体生成工作流。 */
export type DevelopmentPlanningEntity = {
  entityId: string
  key: string
  label: string
  purpose: string
  schemaRef?: string
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
