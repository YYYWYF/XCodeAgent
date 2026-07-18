import type { ApplicationDevelopmentTask, ApplicationMenuItem, ApplicationSharedModule } from './application'

export type DevelopmentPlanningQuestion = { id: string; question: string; rationale: string; placeholder: string }
export type DevelopmentPlanningAnswer = { questionId: string; question: string; answer: string }
export type DevelopmentPlanningPageOption = {
  pageId: string
  key: string
  label: string
  path: string
  purpose: string
  detailPlanStatus?: string
  hasDetailPlan?: boolean
}
export type MenuDevelopmentPlan = { menuKey: string; menuLabel: string; tasks: ApplicationDevelopmentTask[] }

export type ApplicationDevelopmentPlan = {
  schemaVersion: 1
  summary: string
  executionOrder: string[]
  sharedModules: ApplicationSharedModule[]
  menuPlans: MenuDevelopmentPlan[]
}

export type DevelopmentPlanningProgress = { stage: string; message: string; detail: string; percent: number }

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
