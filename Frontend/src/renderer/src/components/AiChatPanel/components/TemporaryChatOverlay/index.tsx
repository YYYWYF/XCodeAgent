import { CloseOutlined, SendOutlined } from '@ant-design/icons'
import { Input } from 'antd'
import type { ReactElement } from 'react'
import { useEffect } from 'react'
import freeChatIcon from '../../../../assets/icons/free-chat.svg'
import { cx } from '../../../../utils'
import './TemporaryChatOverlay.less'

type TemporaryChatOverlayProps = {
  onClose: () => void
}

/** 渲染暂未接入 Agent 的临时对话浮层，并允许用户通过关闭按钮或 Escape 返回工作台。 */
export default function TemporaryChatOverlay({ onClose }: TemporaryChatOverlayProps): ReactElement {
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
          <strong>临时对话</strong>
          <span>辅助主对话解决即时疑问</span>
        </span>
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
          嗨，你好呀！我是你的临时对话助手，页面、接口、代码上的疑问都可以直接问我，也能帮你分析问题、聊聊思路。
        </div>
      </div>

      <div aria-disabled="true" className={cx('temporary-chat-composer')}>
        <Input.TextArea
          aria-label="临时对话输入框，暂不可用"
          autoSize={{ minRows: 1, maxRows: 4 }}
          bordered={false}
          disabled
          placeholder="问问当前应用、产品或代码的问题…"
        />
        <button aria-label="发送消息，暂不可用" disabled title="暂未接入 Agent" type="button">
          <SendOutlined />
        </button>
      </div>
    </aside>
  )
}
