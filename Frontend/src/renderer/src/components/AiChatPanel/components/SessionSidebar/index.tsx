import {
  ArrowLeftOutlined,
  CloseOutlined,
  DeleteOutlined,
  FolderOpenOutlined,
  PlusOutlined,
  SearchOutlined,
  ThunderboltOutlined
} from '@ant-design/icons'
import { Alert, Button, Empty, Input, Popconfirm, Spin, Typography } from 'antd'
import type { KeyboardEvent, ReactElement } from 'react'
import { useMemo, useState } from 'react'
import type { ChatSessionSummary } from '../../../../service/chatSessions'
import type { ApplicationConfig } from '../../../../typings'
import { cx } from '../../../../utils'
import { formatSessionTime } from '../../utils'
import type { SessionRunStatus } from '../../hooks/sessionRuntime'
import './SessionSidebar.less'

const { Text } = Typography

type SessionSidebarProps = {
  activeSessionId?: string
  application: ApplicationConfig
  deletingSessionId?: string
  loadingSessions: boolean
  onCreateSession: () => void
  onDeleteSession: (sessionId: string) => Promise<void>
  onOpenSession: (sessionId: string) => Promise<void>
  onOpenSessionKeyDown: (event: KeyboardEvent<HTMLDivElement>, sessionId: string) => void
  onReturnWelcome: () => void
  onShowSkills: () => void
  sessionError?: string
  sessionRunStates: Record<string, SessionRunStatus>
  sessions: ChatSessionSummary[]
  skillsActive: boolean
  workspaceRoot: string
}

export default function SessionSidebar({
  activeSessionId,
  application,
  deletingSessionId,
  loadingSessions,
  onCreateSession,
  onDeleteSession,
  onOpenSession,
  onOpenSessionKeyDown,
  onReturnWelcome,
  onShowSkills,
  sessionError,
  sessionRunStates,
  sessions,
  skillsActive,
  workspaceRoot
}: SessionSidebarProps): ReactElement {
  const [query, setQuery] = useState('')
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

  return (
    <aside className={cx('session-sidebar')} aria-label="历史会话">
      <div className={cx('session-sidebar-header')}>
        <div className={cx('session-brand-lockup')}>
          <span className={cx('session-brand-mark')} aria-hidden="true"><CloseOutlined /></span>
          <Text className={cx('session-brand')} strong>XCodeAgent</Text>
        </div>
        <Button
          aria-label="返回欢迎页"
          className={cx('session-return-button')}
          icon={<ArrowLeftOutlined />}
          onClick={onReturnWelcome}
          size="small"
          title="返回欢迎页"
          type="text"
        />
      </div>
      <button className={cx('session-workspace')} title={workspaceRoot} type="button">
        <span className={cx('session-workspace-icon')}><FolderOpenOutlined /></span>
        <span>
          <Text type="secondary">工作区</Text>
          <Text className={cx('session-workspace-name')} strong>
            {application.workspaceRoot ? application.name : '未选择工作目录'}
          </Text>
        </span>
      </button>
      <Button
        aria-current={skillsActive ? 'page' : undefined}
        block
        className={cx('session-skills-button', skillsActive && 'active')}
        icon={<ThunderboltOutlined />}
        onClick={onShowSkills}
      >
        技能
      </Button>
      <Button
        block
        className={cx('session-new-button')}
        disabled={!application.workspaceRoot}
        icon={<PlusOutlined />}
        onClick={onCreateSession}
      >
        新对话
      </Button>
      <Input
        allowClear
        aria-label="搜索历史对话"
        className={cx('session-search')}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="搜索历史对话"
        prefix={<SearchOutlined />}
        value={query}
      />
      <div className={cx('session-list')} aria-live="polite">
        {loadingSessions ? (
          <div className={cx('session-loading')}>
            <Spin size="small" />
            <Text type="secondary">读取会话...</Text>
          </div>
        ) : filteredSessions.length === 0 ? (
          <Empty
            description={query ? '没有匹配的对话' : application.workspaceRoot ? '暂无本地会话' : '选择工作目录后保存会话'}
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          />
        ) : (
          sessionGroups.map((group) => (
            <section className={cx('session-group')} key={group.label}>
              <Text className={cx('session-group-label')}>{group.label}</Text>
              {group.sessions.map((session) => {
                const runStatus = sessionRunStates[session.id]
                const running = Boolean(runStatus)
                return (
                  <div
                    aria-current={
                      !skillsActive && activeSessionId === session.id ? 'page' : undefined
                    }
                    className={cx(
                      'session-item',
                      !skillsActive && activeSessionId === session.id && 'active',
                      running && 'running'
                    )}
                    key={session.id}
                  >
                    <div
                      className={cx('session-item-content')}
                      onClick={() => onOpenSession(session.id)}
                      onKeyDown={(event) => onOpenSessionKeyDown(event, session.id)}
                      role="button"
                      tabIndex={0}
                    >
                      <span className={cx('session-item-title')}>{session.title}</span>
                      <span className={cx('session-item-meta')}>
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
                        className={cx('session-delete-button')}
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
    </aside>
  )
}
