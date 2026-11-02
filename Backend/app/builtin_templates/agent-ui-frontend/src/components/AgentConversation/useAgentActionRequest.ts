import React, { useEffect, useRef } from 'react'
import { Modal } from 'antd'
import type { AgentActionRequest, AgentRunResult, AgentUiTemplateConfig } from '@/typings/agentConversation'

/** 将页面组合步骤交给固定组件执行，每个请求只消费一次，等待或失败不会冒充步骤成功。 */
export function useAgentActionRequest(config: AgentUiTemplateConfig, request: AgentActionRequest | undefined,
  onResult: ((requestId: string, result: AgentRunResult) => void) | undefined,
  send: (message: string, actionId: string, runId: string) => Promise<AgentRunResult | undefined>,
  clear: () => Promise<AgentRunResult | undefined>): React.ReactElement {
  const consumed = useRef(new Set<string>())
  const [modal, holder] = Modal.useModal()
  useEffect(() => {
    if (!request || consumed.current.has(request.requestId)) return
    consumed.current.add(request.requestId)
    const action = config.actions?.find((item) => item.actionId === request.actionId && item.stepId === request.stepId)
    /** 用结构化结果结束该次请求，调用方只能在 completed 后推进后续步骤。 */
    function finish(result?: AgentRunResult): void {
      onResult?.(request!.requestId, result || { runId: request!.requestId, threadId: '', status: 'error',
        messages: [], error: '当前会话无法执行该步骤，请处理正在运行或等待的事项后明确重新发起。' })
    }
    /** 仅使用声明操作和固定适配器，不在页面重新实现会话网络调用。 */
    async function execute(): Promise<void> {
      if (!action) { finish(); return }
      if (action.operation === 'clear_conversation') { finish(await clear()); return }
      const message = request!.message ?? action.message
      if (!message.trim()) { finish(); return }
      finish(await send(message, action.actionId, request!.requestId))
    }
    if (action?.requiresConfirmation) {
      modal.confirm({ title: `确认${action.label}？`, okText: '确认', cancelText: '取消',
        onOk: execute, onCancel: () => finish() })
    } else void execute()
  }, [config, request, onResult, send, clear, modal])
  return holder
}
