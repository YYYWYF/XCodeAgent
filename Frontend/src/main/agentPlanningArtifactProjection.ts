import type { BusinessAgentOption } from '../shared/businessAgent'

/** 从磁盘 JSON 中只读取对象数组，不推断或生成产品事实。 */
function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> =>
        Boolean(item && typeof item === 'object' && !Array.isArray(item))
      )
    : []
}

/** 从已确认产品与当前 Runtime 合同投射工作台智能体，拒绝缺失及重复绑定。 */
export function projectWorkbenchAgents(
  product: Record<string, unknown>,
  technical: Record<string, unknown>
): BusinessAgentOption[] {
  const products = records(product.agents)
  return records(technical.agent_contracts).map((contract) => {
    const matches = products.filter((item) => item.agentId === contract.agentId)
    if (matches.length !== 1)
      throw new Error(`智能体 ${String(contract.agentId)} 缺少唯一产品规划。`)
    const item = matches[0]
    const settings = contract.agentSettings as BusinessAgentOption['agentSettings']
    if (!settings || !Array.isArray(contract.pageIds)) throw new Error('智能体技术合同不完整。')
    return {
      agentId: String(contract.agentId),
      label: String(contract.name),
      purpose: String(contract.purpose),
      pageIds: contract.pageIds.map(String),
      capabilities: records(item.capabilities).map((entry) => String(entry.name)),
      boundaries: Array.isArray(item.boundaries) ? item.boundaries.map(String) : [],
      agentSettings: settings,
      tools: records(settings.tools?.bindings).map((binding) => ({
        toolId: String(binding.toolId),
        apiContractId: String(binding.apiContractId),
        endpointId: String(binding.endpointId)
      }))
    }
  })
}
