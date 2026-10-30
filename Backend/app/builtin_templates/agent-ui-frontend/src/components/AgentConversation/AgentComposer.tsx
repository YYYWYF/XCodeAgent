import React from 'react'
import { Button, Input } from 'antd'
import { PaperClipOutlined, SendOutlined, StopOutlined } from '@ant-design/icons'
import { agentIconCompatibilityProps } from './iconCompatibility'

/** 渲染共享输入区；待交互或状态未知时禁止普通提问，运行中提供明确停止入口。 */
export function AgentComposer({ name, attachments, running, blocked, input, onInput, onKeyDown, onStop, onSend }: {
  name: string; attachments: boolean; running: boolean; blocked: boolean; input: string
  onInput: (value: string) => void; onKeyDown: React.KeyboardEventHandler<HTMLTextAreaElement>
  onStop: () => void; onSend: () => void
}): React.ReactElement {
  return <div className="x-agent-composer">
    {attachments && <Button aria-label="附件入口" disabled icon={<PaperClipOutlined {...agentIconCompatibilityProps} />} />}
    <Input.TextArea aria-label={`向${name}提问`} autoSize={{ minRows: 1, maxRows: 4 }}
      disabled={running || blocked} onChange={(event) => onInput(event.target.value)} onKeyDown={onKeyDown}
      placeholder="输入问题，Enter 发送，Shift+Enter 换行" value={input} />
    {running ? <Button icon={<StopOutlined {...agentIconCompatibilityProps} />} onClick={onStop}>停止</Button>
      : <Button disabled={!input.trim() || blocked} icon={<SendOutlined {...agentIconCompatibilityProps} />}
          onClick={onSend} type="primary">发送</Button>}
  </div>
}
