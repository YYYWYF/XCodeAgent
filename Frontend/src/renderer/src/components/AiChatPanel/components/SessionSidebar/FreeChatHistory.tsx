import {
  CloseOutlined,
  CommentOutlined,
  DeleteOutlined,
  InfoCircleOutlined,
  PlusOutlined
} from '@ant-design/icons'
import { Button, Empty, Popconfirm, Spin } from 'antd'
import type { ReactElement } from 'react'
import { useEffect, useRef, useState } from 'react'
import freeChatIcon from '../../../../assets/icons/free-chat.svg'
import type { ChatSessionSummary } from '../../../../service/chatSessions'
import { cx } from '../../../../utils'
import type { SessionRunStatus } from '../../hooks/sessionRuntime'
import '../TemporaryChatOverlay/TemporaryChatOverlay.less'
import ScrollingConversationTitle from './ScrollingConversationTitle'
import './FreeChatHistory.less'

type FreeChatHistoryProps = {
  open: boolean
  historyEnabled: boolean
  activeSessionId?: string
  advancingSessionId?: string
  deletingSessionId?: string
  loadingSessions: boolean
  onClose: () => void
  onCreateSession: () => void
  onCreateTemporaryChat: () => void
  onDeleteTemporaryChat: (sessionId: string) => void
  onDeleteSession: (sessionId: string) => Promise<void>
  onOpenSession: (sessionId: string) => Promise<void>
  onOpenTemporaryChat: (sessionId: string) => void
  sessionCreationDisabled: boolean
  sessionError?: string
  sessionRunStates: Record<string, SessionRunStatus>
  sessions: ChatSessionSummary[]
  temporaryChats: import('./useTemporaryChats').TemporaryChatSession[]
  temporaryChatEnabled: boolean
  theme: 'light' | 'dark'
}

/** 合并常规历史与临时对话空态，未接入临时能力时不生成占位记录。 */
export default function FreeChatHistory({
  open,
  historyEnabled,
  activeSessionId,
  advancingSessionId,
  deletingSessionId,
  loadingSessions,
  onClose,
  onCreateSession,
  onCreateTemporaryChat,
  onDeleteTemporaryChat,
  onDeleteSession,
  onOpenSession,
  onOpenTemporaryChat,
  sessionCreationDisabled,
  sessionError,
  sessionRunStates,
  sessions,
  temporaryChats,
  temporaryChatEnabled
}: FreeChatHistoryProps): ReactElement | null {
  const [tab, setTab] = useState<'regular' | 'temporary'>(historyEnabled ? 'regular' : 'temporary')
  // 面板根节点引用，给"点击外部关闭"判断用。
  const rootRef = useRef<HTMLElement | null>(null)
  // 用 ref 持有最新 onClose，避免监听器因 onClose 引用变化反复挂载/卸载。
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose
  // 常规会话继续按原阶段门控，临时会话在当前 Renderer 生命周期内独立管理。
  useEffect(() => {
    setTab(historyEnabled ? 'regular' : 'temporary')
  }, [historyEnabled])

  useEffect(() => {
    /** 对话管理打开期间响应 Escape 关闭列表。 */
    const handleKeyDown = (event: KeyboardEvent): void => {
      if (open && event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [onClose, open])

  // 点击面板外部自动收起：与其他工作台抽屉（技能/文件/设置）行为一致。
  // 用 mousedown 而非 click：click 在 mouseup 才触发，用户在面板内按下拖到外部松开
  // （选择文本）不应被误判为"点击外部"。
  // 排除 antd 浮层（Popconfirm 删除确认、Tooltip 等）：它们 portal 到 body，DOM 在
  // 面板外但语义上属于面板内的交互，不应触发关闭。
  useEffect(() => {
    if (!open) return
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
        // 侧栏"对话管理"按钮本身是 toggle：mousedown 关闭后 click 又会打开，导致闪烁。
        // 跳过它，让 click 阶段的 toggle 逻辑独立决定打开/关闭。
        if (target.closest('[data-history-toggle="true"]')) return
      }
      onCloseRef.current?.()
    }
    document.addEventListener('mousedown', handleMouseDown, true)
    return () => {
      document.removeEventListener('mousedown', handleMouseDown, true)
    }
  }, [open])

  /** 按当前标签分别创建持久常规会话或内存临时会话。 */
  const handleCreateSession = (): void => {
    if (tab === 'temporary') {
      if (temporaryChatEnabled) onCreateTemporaryChat()
      return
    }
    if (!sessionCreationDisabled && historyEnabled) onCreateSession()
  }

  /** 当前查看或正在推进的会话均保留，浏览历史不会解除推进会话的保护。 */
  const isCurrentSession = (sessionId: string): boolean =>
    sessionId === activeSessionId || sessionId === advancingSessionId

  /** 在确认时再次检查会话归属，阻止打开弹窗后成为当前会话的记录被删除。 */
  const handleDeleteSession = async (sessionId: string): Promise<void> => {
    if (isCurrentSession(sessionId)) return
    await onDeleteSession(sessionId)
  }

  if (!open) return null

  return (
    <section aria-label="对话管理" className={cx('free-chat-history-panel')} ref={rootRef}>
      <header className={cx('free-chat-history-header')}>
        <span aria-hidden="true" className={cx('temporary-chat-agent-icon')}>
          <span
            className={cx('temporary-chat-agent-icon-glyph')}
            style={
              { '--temporary-chat-icon-source': `url("${freeChatIcon}")` } as React.CSSProperties
            }
          />
        </span>
        <span className={cx('temporary-chat-heading')}>
          <strong>对话管理</strong>
          <span>按需拆分上下文，当前阶段仅一条对话可继续推进</span>
        </span>
        <button
          aria-label="关闭对话管理"
          className={cx('temporary-chat-close')}
          onClick={onClose}
          type="button"
        >
          <CloseOutlined />
        </button>
      </header>
      <div className={cx('conversation-management-tabs')}>
        <div role="tablist" aria-label="对话类型">
          <button
            role="tab"
            aria-selected={tab === 'regular'}
            disabled={!historyEnabled}
            className={cx(tab === 'regular' && 'active')}
            onClick={() => setTab('regular')}
            type="button"
          >
            常规对话 <span>{historyEnabled ? sessions.length : 0}</span>
          </button>
          <button
            role="tab"
            aria-selected={tab === 'temporary'}
            className={cx(tab === 'temporary' && 'active')}
            onClick={() => setTab('temporary')}
            type="button"
          >
            临时对话 <span>{temporaryChats.length}</span>
          </button>
        </div>
        <Button
          className={cx('free-chat-history-create')}
          disabled={
            tab === 'temporary'
              ? !temporaryChatEnabled
              : sessionCreationDisabled || !historyEnabled
          }
          icon={<PlusOutlined />}
          onClick={handleCreateSession}
        >
          新建对话
        </Button>
      </div>
      <div
        className={cx('free-chat-history-body')}
        role="tabpanel"
        aria-label={tab === 'regular' ? '常规对话' : '临时对话'}
      >
        <p className={cx('conversation-management-hint')}>
          <InfoCircleOutlined />
          {tab === 'regular'
            ? '仅「当前推进」的对话可执行工作流，其余对话仅供查看。'
            : '临时对话可查阅和讨论当前工程；不会修改文件或发起工作流，关闭后清除。'}
        </p>
        {tab === 'regular' && loadingSessions ? (
          <div className={cx('free-chat-history-loading')}>
            <Spin size="small" />
            读取会话...
          </div>
        ) : tab === 'temporary' && temporaryChats.length > 0 ? (
          <div className={cx('free-chat-history-list')}>
            {temporaryChats.map((session) => (
              <div className={cx('free-chat-history-item')} key={session.id}>
                <button
                  aria-label={`打开临时对话 ${session.number}`}
                  className={cx('free-chat-history-open')}
                  onClick={() => onOpenTemporaryChat(session.id)}
                  type="button"
                >
                  <CommentOutlined />
                  <span className={cx('free-chat-history-title')}>临时对话 {session.number}</span>
                  {session.isRunning ? (
                    <span className={cx('free-chat-history-active-label')}>回答中</span>
                  ) : null}
                </button>
                <Popconfirm
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                  okText="关闭"
                  onConfirm={() => onDeleteTemporaryChat(session.id)}
                  title="关闭并清除这条临时对话？"
                >
                  <Button
                    aria-label={`关闭临时对话 ${session.number}`}
                    danger
                    icon={<DeleteOutlined />}
                    size="small"
                    title="关闭并清除"
                    type="text"
                  />
                </Popconfirm>
              </div>
            ))}
          </div>
        ) : tab === 'regular' && sessions.length === 0 ? (
          <Empty
            description="还没有常规对话"
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          />
        ) : tab === 'regular' ? (
          <div className={cx('free-chat-history-list')}>
            {sessions.map((session) => (
              <div
                className={cx(
                  'free-chat-history-item',
                  session.id === activeSessionId && 'active'
                )}
                key={session.id}
              >
                <button
                  aria-current={activeSessionId === session.id ? 'page' : undefined}
                  className={cx('free-chat-history-open')}
                  onClick={() => {
                    void onOpenSession(session.id)
                  }}
                  type="button"
                >
                  <CommentOutlined />
                  <ScrollingConversationTitle text={session.title} />
                  {session.id === advancingSessionId ? (
                    <span className={cx('free-chat-history-active-label')}>当前推进</span>
                  ) : null}
                </button>
                <Popconfirm
                  cancelText="取消"
                  disabled={isCurrentSession(session.id) || deletingSessionId === session.id}
                  okButtonProps={{ danger: true }}
                  okText="删除"
                  onConfirm={() => handleDeleteSession(session.id)}
                  title={
                    sessionRunStates[session.id]
                      ? '确定删除这个对话并停止正在进行的任务吗？'
                      : '确定删除？'
                  }
                >
                  <Button
                    aria-label={`删除会话 ${session.title}`}
                    danger
                    disabled={
                      isCurrentSession(session.id) ||
                      loadingSessions ||
                      deletingSessionId === session.id
                    }
                    icon={<DeleteOutlined />}
                    loading={deletingSessionId === session.id}
                    size="small"
                    title={isCurrentSession(session.id) ? '当前会话不能删除' : '删除会话'}
                    type="text"
                  />
                </Popconfirm>
              </div>
            ))}
          </div>
        ) : (
          <Empty
            description={temporaryChatEnabled ? '还没有临时对话' : '当前工程暂不可用'}
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          />
        )}
        {tab === 'regular' && sessionError ? (
          <p className={cx('free-chat-history-error')}>{sessionError}</p>
        ) : null}
      </div>
    </section>
  )
}
