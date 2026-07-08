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
  clarification?: WorkflowClarification
  [key: string]: unknown
}

export type WorkflowClarificationQuestion = {
  id?: string
  header?: string
  question?: string
  type?: 'choice' | 'text' | 'yesno'
  dimension?: string
  default_assumption?: string
  placeholder?: string
  multiSelect?: boolean
  options?: Array<{
    label?: string
    description?: string
  }>
}

export type WorkflowClarification = {
  mode?: string
  status?: string
  question_schema?: string
  message?: string
  questions?: WorkflowClarificationQuestion[]
  assumptions?: string[]
  all_unresolved_dimensions?: string[]
  [key: string]: unknown
}

export type WorkflowRunPayload = {
  runId: string
  threadId: string
  summary: WorkflowSummary
  events: WorkflowEvent[]
  state?: Record<string, unknown>
  result?: Record<string, unknown>
}
