/** 当前技术规划中的业务智能体只读投影，与内部执行 Agent 区分。 */
export type BusinessAgentOption = {
  agentId: string
  label: string
  purpose: string
  pageIds: string[]
  capabilities: string[]
  boundaries: string[]
  agentSettings: Record<string, Record<string, unknown>>
  tools: Array<{ toolId: string; apiContractId: string; endpointId: string }>
}
