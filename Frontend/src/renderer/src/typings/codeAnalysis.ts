export type CodeAnalysisStatus = 'in_progress' | 'completed' | 'failed' | 'cancelled'

export type CodeAnalysisProgress = {
  stage: string
  message: string
  detail?: string
  percent: number
}

export type CodeAnalysisToolActivity = {
  callId: string
  tool: string
  category: string
  status: 'running' | 'completed' | 'failed'
  message: string
  path?: string
}

export type CodeAnalysisResult = {
  schemaVersion: 1
  runId: string
  threadId: string
  status: CodeAnalysisStatus
  action: 'scan' | 'get-report'
  progress?: CodeAnalysisProgress
  activeToolActivity?: CodeAnalysisToolActivity
  reportPath?: string
  scannedFiles?: number
  issueCount?: number
  problemFileCount?: number
  severityCounts?: { critical: number; high: number; medium: number; low: number }
  generatedAt?: string
  sizeBytes?: number
  error?: { type?: string; message?: string }
}
