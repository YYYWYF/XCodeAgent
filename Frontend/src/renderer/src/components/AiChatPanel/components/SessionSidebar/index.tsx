import {
  ArrowLeftOutlined,
  DeleteOutlined,
  FolderOpenOutlined,
  MessageOutlined
} from '@ant-design/icons'
import { Alert, Button, Empty, Popconfirm, Spin, Typography } from 'antd'
import type { KeyboardEvent, ReactElement } from 'react'
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
  onCreateSessionKeyDown: (event: KeyboardEvent<HTMLDivElement>) => void
  onDeleteSession: (sessionId: string) => Promise<void>
  onOpenSession: (sessionId: string) => Promise<void>
  onOpenSessionKeyDown: (event: KeyboardEvent<HTMLDivElement>, sessionId: string) => void
  onReturnWelcome: () => void
  sessionError?: string
  sessionRunStates: Record<string, SessionRunStatus>
  sessions: ChatSessionSummary[]
  workspaceRoot: string
}

export default function SessionSidebar({
  activeSessionId,
  application,
  deletingSessionId,
  loadingSessions,
  onCreateSession,
  onCreateSessionKeyDown,
  onDeleteSession,
  onOpenSession,
  onOpenSessionKeyDown,
  onReturnWelcome,
  sessionError,
  sessionRunStates,
  sessions,
  workspaceRoot
}: SessionSidebarProps): ReactElement {
  return (
    <aside className={cx('session-sidebar')} aria-label="历史会话">
      <div className={cx('session-sidebar-header')}>
        <Text strong>历史会话</Text>
        <Button
          aria-label="返回欢迎页"
          className={cx('session-return-button')}
          icon={<ArrowLeftOutlined />}
          onClick={onReturnWelcome}
          size="small"
          title="返回欢迎页"
          type="text"
        >
          返回
        </Button>
      </div>
      <Text className={cx('session-workspace-name')} title={workspaceRoot}>
        <FolderOpenOutlined /> {application.workspaceRoot ? application.name : '未选择工作目录'}
      </Text>
      <div className={cx('session-list')} aria-live="polite">
        <div
          aria-disabled={!application.workspaceRoot}
          className={cx('session-new-entry', !application.workspaceRoot && 'disabled')}
          onClick={onCreateSession}
          onKeyDown={onCreateSessionKeyDown}
          tabIndex={application.workspaceRoot ? 0 : -1}
        >
          <span className={cx('session-item-title')}>
            <MessageOutlined /> 新对话
          </span>
          <span className={cx('session-item-meta')}>创建空白会话</span>
        </div>
        {loadingSessions ? (
          <div className={cx('session-loading')}>
            <Spin size="small" />
            <Text type="secondary">读取会话...</Text>
          </div>
        ) : sessions.length === 0 ? (
          <Empty
            description={application.workspaceRoot ? '暂无本地会话' : '选择工作目录后保存会话'}
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          />
        ) : (
          sessions.map((session) => {
            const runStatus = sessionRunStates[session.id]
            const running = Boolean(runStatus)
            return (
              <div
                className={cx(
                  'session-item',
                  activeSessionId === session.id && 'active',
                  running && 'running'
                )}
                key={session.id}
              >
                <div
                  className={cx('session-item-content')}
                  onClick={() => onOpenSession(session.id)}
                  onKeyDown={(event) => onOpenSessionKeyDown(event, session.id)}
                  tabIndex={0}
                >
                  <span className={cx('session-item-title')}>
                    <MessageOutlined /> {session.title}
                  </span>
                  <span className={cx('session-item-meta')}>
                    {running && <Spin size="small" />}
                    {runStatus === 'stopping'
                      ? '正在停止...'
                      : runStatus === 'running'
                        ? '正在执行...'
                        : `${formatSessionTime(session.updatedAt)} · ${session.messageCount} 条`}
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
          })
        )}
      </div>
      {sessionError && <Alert message={sessionError} showIcon type="error" />}
    </aside>
  )
}
