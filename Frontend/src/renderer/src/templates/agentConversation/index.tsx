import React from 'react'
import { AgentConversationTemplate } from '@xcodeagent/agent-ui-design'

const AGENT_UI_CONFIG_JSON =
  '{"templateVersion":"agent-ui.v1","agentId":"sample_agent","surface":"standalone_page","name":"订单助手","responsibility":"分析订单并协助跟进","actionId":"open_sample_agent","contextItems":[],"capabilities":[{"id":"analyze_orders","label":"分析异常订单"},{"id":"summarize_followups","label":"汇总待跟进事项"}],"suggestedQuestions":["本周有哪些异常订单？","哪些订单需要优先跟进？"],"features":{"attachments":false,"approvals":true,"tools":true,"maximize":false},"mock":{"userMessage":"帮我分析本周需要优先处理的订单。","assistantMessage":"我会先检查逾期、金额异常和待审批订单，再给出优先级建议。","toolTitle":"查询订单","toolDetail":"已读取 18 条当前用户有权查看的订单。","approvalTitle":"需要你的确认","approvalDetail":"是否把 3 条高风险订单标记为优先跟进？","successMessage":"订单分析已完成。","errorMessage":"连接暂时中断，你可以重试，已发送的消息不会丢失。"}}'

/** 使用固定 Agent UI 组件渲染独立会话页模板。 */
function AgentConversationPage(): React.ReactElement {
  return <AgentConversationTemplate configJson={AGENT_UI_CONFIG_JSON} />
}

export default AgentConversationPage
