import React from 'react'
import { Button, Popconfirm } from 'antd'
import type { AgentUiAction } from '@/typings/agentConversation'

/** 根据产品动作渲染推荐提问、追问和客户端清空，确认只授权当前点击。 */
export function AgentActionControls({ actions, disabled, blocked, onAction }: {
  actions: AgentUiAction[]
  disabled: boolean
  blocked: boolean
  onAction: (action: AgentUiAction) => void
}): React.ReactElement {
  return <div className="x-agent-chat__suggestions">
    {actions.map((action) => action.requiresConfirmation ? (
      <Popconfirm key={action.actionId} disabled={disabled || (blocked && action.operation === 'send_message')} title={action.operation === 'clear_conversation'
        ? '切换到空白会话？已有服务端历史和已提交操作会保留。' : `确认${action.label}？`}
        onConfirm={() => onAction(action)} okText="确认" cancelText="取消">
        <Button disabled={disabled || (blocked && action.operation === 'send_message')} data-action-id={action.actionId}>{action.label}</Button>
      </Popconfirm>
    ) : <Button key={action.actionId} disabled={disabled || (blocked && action.operation === 'send_message')} data-action-id={action.actionId}
      onClick={() => onAction(action)}>{action.label}</Button>)}
  </div>
}
