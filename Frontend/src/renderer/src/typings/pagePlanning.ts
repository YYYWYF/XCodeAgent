export type ApplicationPlanningConfirmation = {
  confirmedAt: string
  directories: {
    specs: string
    plans: string
  }
  artifacts: Record<string, Record<string, { format: string; path: string; sha256: string }>>
}
