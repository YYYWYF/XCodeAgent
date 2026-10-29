import { recordItems, textValue, type JsonRecord } from './TechnicalPlanDocPanelData'

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

/** 仅合并阅读视图，每个正式 Agent 对应一个接口，不写入普通业务 API 或实体。 */
export function technicalPlanDisplayContracts(plan: JsonRecord): JsonRecord[] {
  const agentContracts = recordItems(plan.agent_contracts).map((agent): JsonRecord => {
    const invocation = agentInvocation(textValue(agent.agentId))
    return {
      id: invocation.endpointId,
      name: `${textValue(agent.name)} · 智能体接口`,
      base_path: invocation.path,
      invocation,
      agentId: agent.agentId,
      pageIds: agent.pageIds,
      endpoints: [
        {
          id: invocation.endpointId,
          name: '智能体会话',
          method: invocation.method,
          path: invocation.path,
          summary: 'AG-UI SSE 会话接口，所有关联页面共用。'
        }
      ]
    }
  })
  return [...recordItems(plan.api_contracts), ...agentContracts]
}
