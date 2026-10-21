import { ArrowLeftOutlined, CloseOutlined, SendOutlined } from '@ant-design/icons'
import { Button, Input, Spin } from 'antd'
import type { KeyboardEvent, ReactElement } from 'react'
import { useEffect, useRef, useState } from 'react'
import freeChatIcon from '../../../../assets/icons/free-chat.svg'
import type { TemporaryChatMessage } from '../../../../service/temporaryChat'
import { cx } from '../../../../utils'
import MarkdownContent from '../../../MarkdownContent/MarkdownContent'
import type { TemporaryChatSession } from '../SessionSidebar/useTemporaryChats'
import './TemporaryChatOverlay.less'

type TemporaryChatOverlayProps = {
  session: TemporaryChatSession
  onBack: () => void
  onClose: () => void
  onSend: (request: string) => Promise<void>
}

/** 渲染只读临时对话、工程回答和内存中的发送输入框。 */
export default function TemporaryChatOverlay({
  session,
  onBack,
  onClose,
  onSend
}: TemporaryChatOverlayProps): ReactElement {
  const [draft, setDraft] = useState('')
  const messagesEndRef = useRef<HTMLDivElement>(null)
  // 浮层根节点引用，给"点击外部收起"判断用。
  const rootRef = useRef<HTMLElement | null>(null)
  // 用 ref 持有最新 onBack，避免监听器因 onBack 引用变化反复挂载/卸载。
  const onBackRef = useRef(onBack)
  onBackRef.current = onBack

  useEffect(() => {
    /** 在浮层打开期间响应 Escape，按临时对话关闭语义清除当前上下文。 */
    const handleKeyDown = (event: globalThis.KeyboardEvent): void => {
      if (event.key === 'Escape') onClose()
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  // 点击浮层外部自动收起：与其他工作台抽屉（技能/文件/设置/对话管理）行为一致。
  // 收起语义用 onBack（返回对话管理列表），不用 onClose——后者会清除临时对话内容，
  // 用户可能只是误点空白处，不应丢失已输入的对话。
  // 用 mousedown 而非 click：click 在 mouseup 才触发，用户在浮层内按下拖到外部松开
  // （选择文本）不应被误判为"点击外部"。
  // 排除 antd 浮层（Popconfirm/Tooltip/Modal 等）：它们 portal 到 body，DOM 在
  // 浮层外但语义上属于浮层内的交互，不应触发收起。
  useEffect(() => {
    const handleMouseDown = (event: MouseEvent): void => {
      const root = rootRef.current
      if (!root) return
      const target = event.target as Node | null
      if (!target) return
      if (root.contains(target)) return
      if (target instanceof Element) {
        const floating = target.closest(
          '.ant-modal-wrap, .ant-modal-mask, .ant-select-dropdown, ' +
            '.ant-picker-dropdown, .ant-popover, .ant-tooltip, .ant-dropdown, ' +
            '.ant-cascader-dropdown, .ant-tree-select-dropdown, .ant-message, ' +
            '.ant-notification, .ant-popconfirm'
        )
        if (floating) return
      }
      onBackRef.current?.()
    }
    document.addEventListener('mousedown', handleMouseDown, true)
    return () => {
      document.removeEventListener('mousedown', handleMouseDown, true)
    }
  }, [])

  useEffect(() => {
    /** 新消息或请求状态变化后将对话视口滚动到底部。 */
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [session.messages, session.isRunning])

  /** 校验输入后提交给只读 Workspace Assistant，并保留 Shift+Enter 换行。 */
  const handleComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (event.key !== 'Enter' || event.shiftKey) return
    event.preventDefault()
    handleSend()
  }

  /** 清空输入框并将一条消息交给临时会话运行时。 */
  const handleSend = (): void => {
    const request = draft.trim()
    if (!request || session.isRunning) return
    setDraft('')
    void onSend(request)
  }

  return (
    <aside aria-label="临时对话" className={cx('temporary-chat-overlay')} ref={rootRef}>
      <header className={cx('temporary-chat-header')}>
        <span aria-hidden="true" className={cx('temporary-chat-agent-icon')}>
          <span
            className={cx('temporary-chat-agent-icon-glyph')}
            style={
              { '--temporary-chat-icon-source': `url("${freeChatIcon}")` } as React.CSSProperties
            }
          />
        </span>
        <span className={cx('temporary-chat-heading')}>
          <strong>临时对话 {session.number}</strong>
          <span>只读工程问答，不修改文件或发起工作流</span>
        </span>
        <button
          aria-label="返回对话管理"
          className={cx('temporary-chat-close')}
          onClick={onBack}
          title="返回对话管理"
          type="button"
        >
          <ArrowLeftOutlined />
        </button>
        <button
          aria-label="关闭临时对话"
          className={cx('temporary-chat-close')}
          onClick={onClose}
          title="关闭临时对话并清除内容"
          type="button"
        >
          <CloseOutlined />
        </button>
      </header>

      <div aria-live="polite" className={cx('temporary-chat-messages')} role="log">
        {session.messages.length === 0 ? (
          <div className={cx('temporary-chat-welcome')}>
            可以询问当前工程的文件、代码和设计，也可以讨论分析结果。这个临时对话只会读取工程，
            不会写入或删除文件，也不会启动正式工作流。
          </div>
        ) : (
          session.messages.map((message) => renderMessage(message))
        )}
        {session.isRunning ? (
          <div className={cx('temporary-chat-running')}>
            <Spin size="small" />
            正在查阅工程并生成回答…
          </div>
        ) : null}
        <div ref={messagesEndRef} />
      </div>
      <form
        className={cx('temporary-chat-composer')}
        onSubmit={(event) => {
          event.preventDefault()
          handleSend()
        }}
      >
        <div className={cx('temporary-chat-composer-frame')}>
          <Input.TextArea
            aria-label="临时对话消息"
            autoSize={{ minRows: 1, maxRows: 6 }}
            disabled={session.isRunning}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={handleComposerKeyDown}
            placeholder="询问当前工程……"
            value={draft}
          />
          <div className={cx('temporary-chat-composer-footer')}>
            <Button
              aria-label="发送临时对话消息"
              className={cx('temporary-chat-send')}
              disabled={!draft.trim() || session.isRunning}
              htmlType="submit"
              icon={<SendOutlined />}
              loading={session.isRunning}
              shape="circle"
              title="发送"
              type="primary"
            />
          </div>
        </div>
      </form>
    </aside>
  )
}

/** 按消息身份渲染用户文本、助手 Markdown 回答或失败提示。 */
function renderMessage(message: TemporaryChatMessage): ReactElement {
  return (
    <div
      aria-label={message.role === 'user' ? '用户消息' : message.role === 'error' ? '请求失败' : '助手回答'}
      className={cx('temporary-chat-message-row', message.role)}
      key={message.id}
    >
      <div className={cx('temporary-chat-message', message.role)}>
        {message.role === 'assistant' ? (
          <MarkdownContent content={message.content} />
        ) : (
          message.content
        )}
      </div>
    </div>
  )
}
