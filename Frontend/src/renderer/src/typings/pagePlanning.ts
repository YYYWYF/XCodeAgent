import type {
  ApplicationApiDefinition,
  ApplicationMenuItem,
  ApplicationPageInteraction,
  ApplicationTerminal
} from './application'

export type ApplicationPageContext = {
  name: string
  scenario: string
  terminal: ApplicationTerminal
}

export type PagePlanningQuestion = {
  id: string
  question: string
  rationale: string
  placeholder: string
}

export type PagePlanningAnswer = {
  questionId: string
  question: string
  answer: string
}

export type ApplicationPageDefinition = {
  id: string
  name: string
  path: string
  purpose: string
  keyFeatures: string[]
  relatedPageIds: string[]
  apiIds: string[]
  interactions: ApplicationPageInteraction[]
}

export type ApplicationPagePlan = {
  schemaVersion: 1
  application: ApplicationPageContext
  clarifications: PagePlanningAnswer[]
  pages: ApplicationPageDefinition[]
  apis: ApplicationApiDefinition[]
}

export type PagePlanningProgress = {
  stage: string
  message: string
  detail: string
  percent: number
}

export type ConfirmedPagePlan = {
  path: string
  sha256: string
  confirmedAt: string
  menus: {
    homeMenuKey: string
    items: ApplicationMenuItem[]
  }
  apis: ApplicationApiDefinition[]
}
