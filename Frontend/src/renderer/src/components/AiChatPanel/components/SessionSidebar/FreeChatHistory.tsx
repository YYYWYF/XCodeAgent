import { DeleteOutlined, HistoryOutlined } from '@ant-design/icons'
import { Button, Empty, Popconfirm, Popover, Spin, Typography } from 'antd'
import type { ReactElement } from 'react'
import { useState } from 'react'
import type { ChatSessionSummary } from '../../../../service/chatSessions'
import { cx } from '../../../../utils'
import type { SessionRunStatus } from '../../hooks/sessionRuntime'
import { formatSessionTime } from '../../utils'

const { Text } = Typography

type FreeChatHistoryProps = {
  activeSessionId?: string
  deletingSessionId?: string
  loadingSessions: boolean
  onDeleteSession: (sessionId: string) => Promise<void>
  onOpenSession: (sessionId: string) => Promise<void>
  sessionError?: string
  sessionRunStates: Record<string, SessionRunStatus>
  sessions: ChatSessionSummary[]
  theme: 'light' | 'dark'
}

/** 通过按需打开的浮层展示自由对话历史，避免固定列表挤压应用大纲。 */
export default function FreeChatHistory({
  activeSessionId,
  deletingSessionId,
  loadingSessions,
  onDeleteSession,
  onOpenSession,
  sessionError,
  sessionRunStates,
  sessions,
  theme
}: FreeChatHistoryProps): ReactElement {
  const [open, setOpen] = useState(false)
  const historyContent = (
    <div className={cx('free-chat-history-popover-content')}>
      <div className={cx('free-chat-history-popover-header')}>
        <Text strong>最近对话</Text>
        <span className={cx('free-chat-history-popover-count')}>{sessions.length}</span>
      </div>

      {loadingSessions ? (
        <div className={cx('page-session-history-loading')}>
          <Spin size="small" />
          <Text>读取会话...</Text>
        </div>
      ) : sessions.length === 0 ? (
        <Empty description="还没有自由对话" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <div className={cx('page-session-history-list', 'free-chat-history-popover-list')}>
          {sessions.map((session) => {
            const runStatus = sessionRunStates[session.id]
            const active = activeSessionId === session.id
            return (
              <div
                className={cx(
                  'page-session-history-item',
                  active && 'active',
                  runStatus && 'running'
                )}
                key={session.id}
              >
                <button
                  aria-current={active ? 'page' : undefined}
                  className={cx('page-session-history-open')}
                  onClick={() => {
                    setOpen(false)
                    void onOpenSession(session.id)
                  }}
                  type="button"
                >
                  <span className={cx('page-session-history-title')}>{session.title}</span>
                  <span className={cx('page-session-history-meta')}>
                    {runStatus === 'stopping'
                      ? '正在停止...'
                      : runStatus === 'running'
                        ? `运行中 · ${session.messageCount} 条消息`
                        : `${formatSessionTime(session.updatedAt)} · ${session.messageCount} 条消息`}
                  </span>
                </button>
                <Popconfirm
                  cancelText="取消"
                  disabled={Boolean(runStatus)}
                  okButtonProps={{ danger: true }}
                  okText="删除"
                  onConfirm={() => onDeleteSession(session.id)}
                  title="删除这个自由对话？"
                >
                  <Button
                    aria-label={`删除会话 ${session.title}`}
                    danger
                    disabled={loadingSessions || Boolean(runStatus)}
                    icon={<DeleteOutlined />}
                    loading={deletingSessionId === session.id}
                    size="small"
                    title="删除会话"
                    type="text"
                  />
                </Popconfirm>
              </div>
            )
          })}
        </div>
      )}
      {sessionError ? (
        <Text className={cx('page-session-history-error')}>{sessionError}</Text>
      ) : null}
    </div>
  )

  return (
    <Popover
      content={historyContent}
      onOpenChange={setOpen}
      open={open}
      overlayClassName={cx('free-chat-history-popover', theme === 'dark' && 'dark')}
      placement="rightBottom"
      trigger="click"
    >
      <button
        aria-expanded={open}
        aria-label="查看最近自由对话"
        className={cx('free-chat-history-trigger')}
        title="查看最近自由对话"
        type="button"
      >
        <HistoryOutlined />
        {sessions.length > 0 ? (
          <span className={cx('free-chat-history-trigger-count')}>{sessions.length}</span>
        ) : null}
      </button>
    </Popover>
  )
}
