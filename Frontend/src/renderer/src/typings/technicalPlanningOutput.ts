/** 当前请求接收到的技术规划公开正文及真实执行活动。 */
export type TechnicalPlanningOutputEntry = {
  id: string
  kind: 'text' | 'activity' | 'retry'
  text: string
  timestamp: string
  status?: 'running' | 'completed' | 'failed'
}

export type TechnicalPlanningOutput = {
  entries: TechnicalPlanningOutputEntry[]
  truncated: boolean
}
