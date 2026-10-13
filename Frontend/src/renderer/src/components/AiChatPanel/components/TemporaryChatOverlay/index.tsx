import { ArrowLeftOutlined, CloseOutlined } from '@ant-design/icons'
import type { ReactElement } from 'react'
import { useEffect } from 'react'
import freeChatIcon from '../../../../assets/icons/free-chat.svg'
import { cx } from '../../../../utils'
import './TemporaryChatOverlay.less'

type TemporaryChatOverlayProps = {
  number: number
  onBack: () => void
  onClose: () => void
}

/** 渲染暂未接入 Agent 的临时对话浮层，并允许用户通过关闭按钮或 Escape 返回工作台。 */
export default function TemporaryChatOverlay({
  number,
  onBack,
  onClose
}: TemporaryChatOverlayProps): ReactElement {
  useEffect(() => {
    /** 在浮层打开期间响应 Escape，避免用户必须移动鼠标才能退出。 */
    const handleKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') onClose()
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  return (
    <aside aria-label="临时对话" className={cx('temporary-chat-overlay')}>
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
          <strong>临时对话 {number}</strong>
          <span>查阅与 Chat 类事务，不触发 Workflow 或写入工作区</span>
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
          title="关闭临时对话"
          type="button"
        >
          <CloseOutlined />
        </button>
      </header>

      <div className={cx('temporary-chat-messages')} role="log">
        <div className={cx('temporary-chat-message', 'assistant')}>
          这是一个新的临时对话。你可以查阅、分析和讨论，但不能从这里发起正式工作流。
        </div>
      </div>
    </aside>
  )
}
