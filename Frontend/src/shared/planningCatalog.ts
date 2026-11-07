export type PlanningCatalogIssue = {
  source: 'product' | 'technical'
  reason: 'missing' | 'invalid' | 'unreadable'
}

export type PlanningCatalogStatus = {
  loading: boolean
  productAvailable: boolean
  technicalAvailable: boolean
  issues: PlanningCatalogIssue[]
  error?: string
}
