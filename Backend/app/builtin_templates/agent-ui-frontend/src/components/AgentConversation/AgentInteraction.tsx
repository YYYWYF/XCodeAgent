import React, { useState } from 'react'
import { Alert, Button, Input, Space } from 'antd'
import type { PendingAgentInteraction } from '@/typings/agentConversation'

/** 仅展示并提交服务端当前交互；澄清文本和严格批准/拒绝保持不同语义。 */
export function AgentInteraction({ pending, disabled, onRespond }: {
  pending: PendingAgentInteraction; disabled: boolean; onRespond: (value: unknown) => void
}): React.ReactElement {
  const [answer, setAnswer] = useState('')
  return <Alert showIcon type="info" message={pending.message} description={
    <Space direction="vertical" style={{ width: '100%' }}>
      {Object.keys(pending.payload).length > 0 && <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(pending.payload, null, 2)}</pre>}
      {pending.kind === 'clarification' ? <>
        <Input.TextArea aria-label="补充回答" value={answer} disabled={disabled} onChange={(event) => setAnswer(event.target.value)} />
        <Button type="primary" disabled={disabled || !answer.trim()} onClick={() => onRespond(answer)}>提交回答</Button>
      </> : <Space>
        <Button type="primary" disabled={disabled} onClick={() => onRespond(true)}>确认</Button>
        <Button disabled={disabled} onClick={() => onRespond(false)}>拒绝</Button>
      </Space>}
    </Space>
  } />
}
