import { Alert, Button, Input, Space, Typography } from 'antd'
import { SendOutlined } from '@ant-design/icons'
import type { DebugConversation } from '../../../../service/runtimeDebugConversation'
import MarkdownContent from '../../../MarkdownContent/MarkdownContent'
import DebugJsonEditor from './DebugJsonEditor'
import AgentDebugTrace from './AgentDebugTrace'

/** 按会话或单次任务展示同一 AG-UI 流，交互确认仍显式交给用户。 */
export default function AgentDebugStreamView({ name, view, taskMode, ready, busy, expired, restartRequired,
  draft, onDraftChange, onSend, onNew, onStop, inputSchema, inputInvalid }: {
  name: string; view: DebugConversation; taskMode: boolean; ready: boolean; busy: boolean; expired: boolean
  restartRequired: boolean; draft: string; onDraftChange: (value: string) => void
  onSend: (answer?: string | boolean) => void; onNew: () => void; onStop: () => void
  inputSchema?: Record<string, unknown> | null; inputInvalid?: boolean
}): JSX.Element {
  const running = view.status === 'running'
  const locked = !ready || busy || expired || running || restartRequired || view.status === 'unknown'
    || Boolean(view.pending && view.pending.kind !== 'clarification')
  return <div className="agent-debug-workspace">
    <section className="agent-debug-conversation" aria-label={taskMode ? '任务执行' : '调试对话'}>
      <div className="agent-debug-section-heading"><h4>{taskMode ? '任务执行' : '调试对话'}</h4>
        <Button type="text" size="small" disabled={!ready || busy || expired || running} onClick={onNew}>新建会话</Button></div>
      <div className={`agent-debug-messages${!view.messages.length ? ' is-empty' : ''}`} role="log" aria-live="polite" aria-relevant="additions text">
        {!view.messages.length && <div className="agent-debug-empty"><h4>{taskMode ? '执行一次业务任务' : '开始一次调试'}</h4>
          <p>{ready ? taskMode ? '填写业务 JSON 输入，查看执行事件与最终结果。' : '输入问题，查看智能体回复。'
            : '启动调试环境后，发送请求并查看执行结果。'}</p></div>}
        {view.messages.map((item) => <article className={`agent-debug-message is-${item.role === 'user' ? 'user' : 'assistant'}`} key={item.id}>
          <strong className="agent-debug-message-author">{item.role === 'user' ? '我' : name}</strong>{item.role === 'user' ? <p>{item.content}</p>
            : <MarkdownContent className="agent-debug-markdown" content={item.content} />}</article>)}
        {view.result !== undefined && view.result !== null && <article className="agent-debug-message"><strong>结构化结果</strong><pre>{JSON.stringify(view.result, null, 2)}</pre></article>}
      </div>
      <section className="agent-debug-composer" aria-label={taskMode ? '任务输入' : '调试消息输入'}>
        {view.pending && <Alert type="warning" showIcon message={view.pending.message} description={view.pending.kind === 'clarification'
          ? '请在下方输入回答并提交。' : <Space><Button disabled={!ready || running || busy || expired || restartRequired} onClick={() => onSend(true)}>同意</Button>
            <Button disabled={!ready || running || busy || expired || restartRequired} onClick={() => onSend(false)}>拒绝</Button></Space>} />}
        <div className="agent-debug-input-shell">
        {taskMode && !view.pending ? <DebugJsonEditor compact label="业务输入 JSON" value={draft} onChange={onDraftChange}
          disabled={locked} schema={inputSchema} objectOnly />
          : <Input.TextArea aria-label="调试消息" autoSize={{ minRows: 2, maxRows: 8 }} maxLength={8000} value={draft}
            onChange={(event) => onDraftChange(event.target.value)} disabled={locked} placeholder="输入问题或澄清回答…" />}
        <div className="agent-debug-composer-footer"><Typography.Text type="secondary">会话仅在当前面板保留</Typography.Text>
          <Space size={8}>{running && <Button onClick={onStop}>停止接收</Button>}
            <Button type="primary" icon={<SendOutlined />} disabled={locked || !draft.trim() || Boolean(inputInvalid)}
              onClick={() => onSend(view.pending ? draft : undefined)}>{view.pending ? '提交回答' : taskMode ? '执行任务' : '发送'}</Button></Space></div>
        </div>
      </section>
    </section>
    <AgentDebugTrace view={view} />
  </div>
}
