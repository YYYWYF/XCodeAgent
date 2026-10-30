import { MockAgentConversationAdapter } from './agentConversationMock'
import { RuntimeAgentConversationAdapter } from './agentConversationRuntime'
import type { AgentConversationAdapter, AgentUiTemplateConfig } from '@/typings/agentConversation'

/** 按明确的正式运行绑定选择适配器；设计预览没有该绑定，保持离线模拟。 */
export function createAgentConversationAdapter(config: AgentUiTemplateConfig): AgentConversationAdapter {
  if (config.runtime) return new RuntimeAgentConversationAdapter(config)
  return new MockAgentConversationAdapter({
    agentName: config.name, assistantMessage: config.mock.assistantMessage,
    toolTitle: config.mock.toolTitle, toolDetail: config.mock.toolDetail,
    approvalTitle: config.mock.approvalTitle, approvalDetail: config.mock.approvalDetail,
    successMessage: config.mock.successMessage, errorMessage: config.mock.errorMessage
  })
}
