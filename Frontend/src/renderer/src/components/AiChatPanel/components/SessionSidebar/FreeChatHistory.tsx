import { CloseOutlined, DeleteOutlined, HistoryOutlined, PlusOutlined } from '@ant-design/icons'
import { Button, Empty, Popconfirm, Spin, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { ChatSessionSummary } from '../../../../service/chatSessions'
import { cx } from '../../../../utils'
import type { SessionRunStatus } from '../../hooks/sessionRuntime'
import { formatSessionTime } from '../../utils'
import './FreeChatHistory.less'

const { Text } = Typography

type FreeChatHistoryProps = {
  activeSessionId?: string
  deletingSessionId?: string
  loadingSessions: boolean
  onClose: () => void
  onCreateSession: () => void
  onDeleteSession: (sessionId: string) => Promise<void>
  onOpenSession: (sessionId: string) => Promise<void>
  sessionError?: string
  sessionRunStates: Record<string, SessionRunStatus>
  sessions: ChatSessionSummary[]
  theme: 'light' | 'dark'
}

/** 以全高侧栏展示当前阶段的自由对话历史，并真实占用工作台横向空间。 */
export default function FreeChatHistory({
  activeSessionId,
  deletingSessionId,
  loadingSessions,
  onClose,
  onCreateSession,
  onDeleteSession,
  onOpenSession,
  sessionError,
  sessionRunStates,
  sessions,
  theme
}: FreeChatHistoryProps): ReactElement {
  return (
    <section
      aria-label="历史对话"
      className={cx('free-chat-history-panel', theme === 'dark' && 'dark')}
    >
      <header className={cx('free-chat-history-header')}>
        <div className={cx('free-chat-history-heading-row')}>
          <div className={cx('free-chat-history-heading')}>
            <span className={cx('free-chat-history-heading-icon')}>
              <HistoryOutlined />
            </span>
            <div>
              <Text strong>历史对话</Text>
              <Text>{sessions.length > 0 ? `${sessions.length} 个自由对话` : '自由对话记录'}</Text>
            </div>
          </div>
          <Button
            aria-label="关闭历史对话"
            className={cx('free-chat-history-close')}
            icon={<CloseOutlined />}
            onClick={onClose}
            size="small"
            title="关闭历史对话"
            type="text"
          />
        </div>
        <Button
          className={cx('free-chat-history-create')}
          icon={<PlusOutlined />}
          onClick={onCreateSession}
          size="small"
          type="default"
        >
          新建对话
        </Button>
      </header>

      <div className={cx('free-chat-history-body')}>
        {loadingSessions ? (
          <div className={cx('free-chat-history-loading')}>
            <Spin size="small" />
            <Text>读取会话...</Text>
          </div>
        ) : sessions.length === 0 ? (
          <Empty description="还没有自由对话" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <div className={cx('free-chat-history-list')}>
            {sessions.map((session) => {
              const runStatus = sessionRunStates[session.id]
              const active = activeSessionId === session.id
              return (
                <div
                  className={cx(
                    'free-chat-history-item',
                    active && 'active',
                    runStatus && 'running'
                  )}
                  key={session.id}
                >
                  <button
                    aria-current={active ? 'page' : undefined}
                    className={cx('free-chat-history-open')}
                    onClick={() => {
                      void onOpenSession(session.id)
                    }}
                    type="button"
                  >
                    <span className={cx('free-chat-history-title-row')}>
                      <span className={cx('free-chat-history-title')}>{session.title}</span>
                      {active ? (
                        <span className={cx('free-chat-history-active-label')}>当前</span>
                      ) : null}
                    </span>
                    <span className={cx('free-chat-history-meta')}>
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
          <Text className={cx('free-chat-history-error')}>{sessionError}</Text>
        ) : null}
      </div>
    </section>
  )
}
