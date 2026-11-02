import { recordItems, type JsonRecord } from './TechnicalPlanDocPanelData'

export type AgentInvocation = {
  protocol: 'ag-ui'
  transport: 'sse'
  serviceId: 'agent-runtime'
  endpointId: string
  method: 'POST'
  path: string
  exposure: 'public'
  requestType: 'RunAgentInput'
  responseType: 'BaseEvent'
  principalSource: 'runtime_anonymous_session'
}

/** 按当前 Runtime 固定合同派生调用入口，与后端 agent_invocation 保持一致。 */
export function agentInvocation(agentId: string): AgentInvocation {
  return {
    protocol: 'ag-ui',
    transport: 'sse',
    serviceId: 'agent-runtime',
    endpointId: `agent.${agentId}.run`,
    method: 'POST',
    path: `/agents/${agentId}/run`,
    exposure: 'public',
    requestType: 'RunAgentInput',
    responseType: 'BaseEvent',
    principalSource: 'runtime_anonymous_session'
  }
}

/** 只读取正式接口集合，运行入口已由规划编译，不额外派生或重复计数。 */
export function technicalPlanDisplayContracts(plan: JsonRecord): JsonRecord[] {
  return recordItems(plan.api_contracts)
}
