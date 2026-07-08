export type WorkflowEvent = {
  type: string
  runId?: string
  threadId?: string
  nodeName?: string
  status?: string
  message?: string
  data?: Record<string, unknown>
  timestamp?: string
}

export type WorkflowSummary = {
  status?: string
  message?: string
  phase?: string
  artifacts?: Record<string, string>
  [key: string]: unknown
}

export type WorkflowRunPayload = {
  runId: string
  threadId: string
  summary: WorkflowSummary
  events: WorkflowEvent[]
  result?: Record<string, unknown>
}
