import {
  DeleteOutlined,
  DownOutlined,
  HistoryOutlined,
  PlusOutlined,
  SearchOutlined
} from '@ant-design/icons'
import { Alert, Button, Dropdown, Empty, Input, Popconfirm, Spin, Typography } from 'antd'
import type { KeyboardEvent, ReactElement } from 'react'
import { useMemo, useState } from 'react'
import type { ChatSessionSummary } from '../../../../service/chatSessions'
import { cx } from '../../../../utils'
import type { SessionRunStatus } from '../../hooks/sessionRuntime'
import { formatSessionTime } from '../../utils'
import './SessionHistoryDropdown.less'

const { Text } = Typography

type SessionHistoryDropdownProps = {
  activeSessionId?: string
  deletingSessionId?: string
  loadingSessions: boolean
  onCreateSession: () => void
  onDeleteSession: (sessionId: string) => Promise<void>
  onOpenSession: (sessionId: string) => Promise<void>
  onOpenSessionKeyDown: (event: KeyboardEvent<HTMLDivElement>, sessionId: string) => void
  sessionError?: string
  sessionRunStates: Record<string, SessionRunStatus>
  sessions: ChatSessionSummary[]
  theme: 'light' | 'dark'
  workspaceSelected: boolean
}

export default function SessionHistoryDropdown({
  activeSessionId,
  deletingSessionId,
  loadingSessions,
  onCreateSession,
  onDeleteSession,
  onOpenSession,
  onOpenSessionKeyDown,
  sessionError,
  sessionRunStates,
  sessions,
  theme,
  workspaceSelected
}: SessionHistoryDropdownProps): ReactElement {
  const [query, setQuery] = useState('')
  const [visible, setVisible] = useState(false)
  const filteredSessions = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase()
    return normalizedQuery
      ? sessions.filter((session) => session.title.toLocaleLowerCase().includes(normalizedQuery))
      : sessions
  }, [query, sessions])
  const todayStart = new Date().setHours(0, 0, 0, 0)
  const sessionGroups = [
    { label: '今天', sessions: filteredSessions.filter((session) => session.updatedAt >= todayStart) },
    { label: '更早', sessions: filteredSessions.filter((session) => session.updatedAt < todayStart) }
  ].filter((group) => group.sessions.length > 0)

  const handleCreate = (): void => {
    setVisible(false)
    onCreateSession()
  }

  const handleOpen = async (sessionId: string): Promise<void> => {
    setVisible(false)
    await onOpenSession(sessionId)
  }

  const overlay = (
    <div
      className={cx('session-history-dropdown', theme === 'dark' && 'dark')}
      onClick={(event) => event.stopPropagation()}
    >
      <div className={cx('session-history-dropdown-header')}>
        <Text strong>历史会话</Text>
        <Button
          disabled={!workspaceSelected}
          icon={<PlusOutlined />}
          onClick={handleCreate}
          size="small"
          type="text"
        >
          新对话
        </Button>
      </div>
      <Input
        allowClear
        aria-label="搜索历史对话"
        onChange={(event) => setQuery(event.target.value)}
        placeholder="搜索历史对话"
        prefix={<SearchOutlined />}
        size="small"
        value={query}
      />
      <div className={cx('session-history-dropdown-list')} aria-live="polite">
        {loadingSessions ? (
          <div className={cx('session-history-dropdown-loading')}>
            <Spin size="small" />
            <Text>读取会话...</Text>
          </div>
        ) : filteredSessions.length === 0 ? (
          <Empty
            description={query ? '没有匹配的对话' : workspaceSelected ? '暂无本地会话' : '选择工作目录后保存会话'}
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          />
        ) : (
          sessionGroups.map((group) => (
            <section className={cx('session-history-dropdown-group')} key={group.label}>
              <Text className={cx('session-history-dropdown-group-label')}>{group.label}</Text>
              {group.sessions.map((session) => {
                const runStatus = sessionRunStates[session.id]
                const running = Boolean(runStatus)
                const active = activeSessionId === session.id
                return (
                  <div
                    aria-current={active ? 'page' : undefined}
                    className={cx('session-history-dropdown-item', active && 'active', running && 'running')}
                    key={session.id}
                  >
                    <div
                      className={cx('session-history-dropdown-item-content')}
                      onClick={() => handleOpen(session.id)}
                      onKeyDown={(event) => {
                        if (event.key === 'Enter' || event.key === ' ') setVisible(false)
                        onOpenSessionKeyDown(event, session.id)
                      }}
                      role="button"
                      tabIndex={0}
                    >
                      <span className={cx('session-history-dropdown-item-title')}>{session.title}</span>
                      <span className={cx('session-history-dropdown-item-meta')}>
                        {running && <Spin size="small" />}
                        {runStatus === 'stopping'
                          ? '正在停止...'
                          : runStatus === 'running'
                            ? `运行中 · ${session.messageCount} 条消息`
                            : `${formatSessionTime(session.updatedAt)} · ${session.messageCount} 条消息`}
                      </span>
                    </div>
                    <Popconfirm
                      cancelText="取消"
                      disabled={running}
                      okText="删除"
                      okButtonProps={{ danger: true }}
                      onCancel={(event) => event?.stopPropagation()}
                      onConfirm={(event) => {
                        event?.stopPropagation()
                        return onDeleteSession(session.id)
                      }}
                      title="删除这个历史会话？"
                    >
                      <Button
                        aria-label={`删除会话 ${session.title}`}
                        className={cx('session-history-dropdown-delete')}
                        danger
                        disabled={loadingSessions || running}
                        icon={<DeleteOutlined />}
                        loading={deletingSessionId === session.id}
                        onClick={(event) => event.stopPropagation()}
                        size="small"
                        title="删除会话"
                        type="text"
                      />
                    </Popconfirm>
                  </div>
                )
              })}
            </section>
          ))
        )}
      </div>
      {sessionError && <Alert message={sessionError} showIcon type="error" />}
    </div>
  )

  return (
    <Dropdown
      onVisibleChange={setVisible}
      overlay={overlay}
      placement="bottomRight"
      trigger={['click']}
      visible={visible}
    >
      <Button
        aria-label="历史会话"
        className={cx('session-history-trigger')}
        icon={<HistoryOutlined />}
        title="历史会话"
      >
        历史会话 <DownOutlined />
      </Button>
    </Dropdown>
  )
}
