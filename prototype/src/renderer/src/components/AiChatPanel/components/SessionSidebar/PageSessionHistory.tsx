import {
  CaretDownOutlined,
  DeleteOutlined,
  HistoryOutlined,
  PlusOutlined
} from '@ant-design/icons'
import { Button, Empty, Popconfirm, Spin, Typography } from 'antd'
import type { ReactElement } from 'react'
import { useState } from 'react'
import type { ChatSessionSummary } from '../../../../service/chatSessions'
import { cx } from '../../../../utils'
import type { SessionRunStatus } from '../../hooks/sessionRuntime'
import { formatSessionTime } from '../../utils'

const { Text } = Typography

type PageSessionHistoryProps = {
  activeSessionId?: string
  deletingSessionId?: string
  loadingSessions: boolean
  onCreateSession: () => Promise<void>
  onDeleteSession: (sessionId: string) => Promise<void>
  onOpenSession: (sessionId: string) => Promise<void>
  deleteTitle?: string
  emptyDescription?: string
  targetLabel: string
  sessionError?: string
  sessionRunStates: Record<string, SessionRunStatus>
  sessions: ChatSessionSummary[]
}

/** 渲染单个页面或 API endpoint 专属的可展开历史会话入口与内联列表。 */
export default function PageSessionHistory({
  activeSessionId,
  deletingSessionId,
  loadingSessions,
  onCreateSession,
  onDeleteSession,
  onOpenSession,
  deleteTitle = '删除这个会话？',
  emptyDescription = '当前对象暂无历史会话',
  targetLabel,
  sessionError,
  sessionRunStates,
  sessions
}: PageSessionHistoryProps): ReactElement {
  const [expanded, setExpanded] = useState(false)

  return (
    <>
      <button
        aria-expanded={expanded}
        aria-label={`${targetLabel}的历史会话`}
        className={cx('page-session-history-toggle', expanded && 'expanded')}
        onClick={() => setExpanded((current) => !current)}
        title={`${targetLabel}的历史会话`}
        type="button"
      >
        <HistoryOutlined />
        <span>{sessions.length}</span>
        <CaretDownOutlined className={cx(!expanded && 'collapsed')} />
      </button>

      {expanded ? (
        <div className={cx('page-session-history-panel')}>
          <div className={cx('page-session-history-header')}>
            <Text>历史会话</Text>
            <Button
              icon={<PlusOutlined />}
              onClick={() => onCreateSession().catch(() => undefined)}
              size="small"
              type="text"
            >
              新建会话
            </Button>
          </div>

          {loadingSessions ? (
            <div className={cx('page-session-history-loading')}>
              <Spin size="small" />
              <Text>读取会话...</Text>
            </div>
          ) : sessions.length === 0 ? (
            <Empty
              description={emptyDescription}
              image={Empty.PRESENTED_IMAGE_SIMPLE}
            />
          ) : (
            <div className={cx('page-session-history-list')}>
              {sessions.map((session) => {
                const runStatus = sessionRunStates[session.id]
                const running = Boolean(runStatus)
                const active = activeSessionId === session.id
                return (
                  <div
                    className={cx(
                      'page-session-history-item',
                      active && 'active',
                      running && 'running'
                    )}
                    key={session.id}
                  >
                    <button
                      aria-current={active ? 'page' : undefined}
                      className={cx('page-session-history-open')}
                      onClick={() => void onOpenSession(session.id)}
                      type="button"
                    >
                      <span className={cx('page-session-history-title')}>{session.title}</span>
                      <span className={cx('page-session-history-meta')}>
                        {running ? <Spin size="small" /> : null}
                        {runStatus === 'stopping'
                          ? '正在停止...'
                          : runStatus === 'running'
                            ? `运行中 · ${session.messageCount} 条消息`
                            : `${formatSessionTime(session.updatedAt)} · ${session.messageCount} 条消息`}
                      </span>
                    </button>
                    <Popconfirm
                      cancelText="取消"
                      disabled={running}
                      okButtonProps={{ danger: true }}
                      okText="删除"
                      onConfirm={() => onDeleteSession(session.id)}
                      title={deleteTitle}
                    >
                      <Button
                        aria-label={`删除会话 ${session.title}`}
                        danger
                        disabled={loadingSessions || running}
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
          {sessionError ? <Text className={cx('page-session-history-error')}>{sessionError}</Text> : null}
        </div>
      ) : null}
    </>
  )
}
