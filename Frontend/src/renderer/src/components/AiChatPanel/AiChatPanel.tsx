import {
  ArrowLeftOutlined,
  CloseOutlined,
  DeleteOutlined,
  DesktopOutlined,
  DownOutlined,
  ExportOutlined,
  FolderOpenOutlined,
  HolderOutlined,
  MessageOutlined,
  RobotOutlined,
  SendOutlined,
  UserOutlined
} from '@ant-design/icons'
import {
  Alert,
  Button,
  Dropdown,
  Empty,
  Input,
  Popconfirm,
  Spin,
  Tag,
  Typography,
  message as antdMessage
} from 'antd'
import type { MenuProps } from 'antd'
import type {
  CSSProperties,
  KeyboardEvent,
  MouseEvent as ReactMouseEvent,
  ReactElement
} from 'react'
import { useEffect, useRef, useState } from 'react'
import { useWorkbench } from '../../context'
import { AgUiChatSession } from '../../service/agUiAgent'
import {
  createChatSessionId,
  createChatSessionTitle,
  deleteChatSession,
  listChatSessions,
  readChatSession,
  saveChatSession,
  type ChatSessionMessage,
  type ChatSessionRecord,
  type ChatSessionSummary
} from '../../service/chatSessions'
import type { ApplicationConfig, EditorMode, WorkflowRunPayload } from '../../typings'
import { cx, getInitialPreviewUrl, openPreviewWindow } from '../../utils'
import BrowserPreviewPanel from '../BrowserPreviewPanel/BrowserPreviewPanel'
import MarkdownContent from '../MarkdownContent/MarkdownContent'
import './AiChatPanel.less'

const { Text, Title } = Typography
const { TextArea } = Input

const DEFAULT_ASSISTANT_PANEL_WIDTH = 660
const MIN_ASSISTANT_PANEL_WIDTH = 520
const MIN_RIGHT_PANEL_WIDTH = 380
const SPLIT_HANDLE_WIDTH = 10

type AgentChatMessage = {
  id: number
  role: 'user' | 'assistant'
  content: string
  workflow?: WorkflowRunPayload
  createdAt: number
}

type RightPanelState = { type: 'preview' }

type Props = {
  application: ApplicationConfig
  editorMode: EditorMode
  onReturnWelcome: () => void
}

const chatCopy: Record<
  EditorMode,
  { title: string; description: string; empty: string; placeholder: string; label: string }
> = {
  frontend: {
    title: '应用开发助手',
    description: '通过 Workflow 统一推进需求判断、计划、构建、验证和交付。',
    empty: '暂无 Workflow 输出',
    placeholder: '输入你想开发或修改的应用需求...',
    label: 'Workflow 输出'
  },
  backend: {
    title: '应用开发助手',
    description: '通过 Workflow 统一推进接口、数据模型、服务逻辑和验证。',
    empty: '暂无 Workflow 输出',
    placeholder: '输入接口、服务或应用开发需求...',
    label: 'Workflow 输出'
  }
}

export default function AiChatPanel({
  application,
  editorMode,
  onReturnWelcome
}: Props): ReactElement {
  const panelRef = useRef<HTMLElement | null>(null)
  const [drafts, setDrafts] = useState<Record<EditorMode, string>>({
    frontend: '',
    backend: ''
  })
  const [agentMessages, setAgentMessages] = useState<Record<EditorMode, AgentChatMessage[]>>({
    frontend: [],
    backend: []
  })
  const [sessionSummaries, setSessionSummaries] = useState<
    Record<EditorMode, ChatSessionSummary[]>
  >({
    frontend: [],
    backend: []
  })
  const [activeSessionIds, setActiveSessionIds] = useState<Partial<Record<EditorMode, string>>>({})
  const [sessionLoadingModes, setSessionLoadingModes] = useState<
    Partial<Record<EditorMode, boolean>>
  >({})
  const [sessionErrors, setSessionErrors] = useState<Partial<Record<EditorMode, string>>>({})
  const [deletingSessionIds, setDeletingSessionIds] = useState<Partial<Record<EditorMode, string>>>(
    {}
  )
  const agUiSessionsRef = useRef<Partial<Record<EditorMode, AgUiChatSession>>>({})
  const [loadingModes, setLoadingModes] = useState<Partial<Record<EditorMode, boolean>>>({})
  const [errors, setErrors] = useState<Partial<Record<EditorMode, string>>>({})
  const [previewError, setPreviewError] = useState('')
  const [rightPanel, setRightPanel] = useState<RightPanelState>()
  const [assistantPanelWidth, setAssistantPanelWidth] = useState(DEFAULT_ASSISTANT_PANEL_WIDTH)
  const [splitDragging, setSplitDragging] = useState(false)
  const { publishAiMessage } = useWorkbench()
  const messages = agentMessages[editorMode]
  const sessions = sessionSummaries[editorMode]
  const activeSessionId = activeSessionIds[editorMode]
  const copy = chatCopy[editorMode]
  const draft = drafts[editorMode]
  const loading = Boolean(loadingModes[editorMode])
  const loadingSessions = Boolean(sessionLoadingModes[editorMode])
  const deletingSessionId = deletingSessionIds[editorMode]
  const error = errors[editorMode]
  const sessionError = sessionErrors[editorMode]
  const showPreviewActions = editorMode === 'frontend'
  const workspaceRoot = application.workspaceRoot || '未选择工作目录'
  const embeddedPreviewOpen = rightPanel?.type === 'preview'
  const rightPanelOpen = Boolean(rightPanel)
  const panelStyle = rightPanelOpen
    ? ({
        '--assistant-panel-width': `${assistantPanelWidth}px`
      } as CSSProperties)
    : undefined

  useEffect(() => {
    loadSessionsForMode(editorMode)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [application.workspaceRoot, editorMode])

  useEffect(() => {
    if (!rightPanelOpen) {
      setSplitDragging(false)
      return
    }

    const nextWidth = clampAssistantPanelWidth(assistantPanelWidth, panelRef.current)
    if (nextWidth !== assistantPanelWidth) {
      setAssistantPanelWidth(nextWidth)
    }
  }, [assistantPanelWidth, rightPanelOpen])

  useEffect(() => {
    if (!splitDragging) return undefined

    const previousCursor = document.body.style.cursor
    const previousUserSelect = document.body.style.userSelect
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    const handleMouseMove = (event: MouseEvent): void => {
      const panelRect = panelRef.current?.getBoundingClientRect()
      if (!panelRect) return

      setAssistantPanelWidth(
        clampAssistantPanelWidth(event.clientX - panelRect.left, panelRef.current)
      )
    }
    const handleMouseUp = (): void => setSplitDragging(false)

    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)

    return () => {
      document.body.style.cursor = previousCursor
      document.body.style.userSelect = previousUserSelect
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
    }
  }, [splitDragging])

  const previewMenuItems: MenuProps['items'] = [
    {
      key: 'external',
      icon: <ExportOutlined />,
      label: '打开网页预览'
    },
    {
      key: 'embedded',
      icon: <DesktopOutlined />,
      label: '打开内嵌页面预览'
    }
  ]

  const handlePreviewAction: MenuProps['onClick'] = async ({ key }) => {
    setPreviewError('')

    if (key === 'embedded') {
      setRightPanel({ type: 'preview' })
      return
    }

    try {
      await openPreviewWindow(getInitialPreviewUrl(application.id))
    } catch (caughtError) {
      setPreviewError(caughtError instanceof Error ? caughtError.message : '无法打开网页预览')
    }
  }

  const handlePanelSplitDragStart = (event: ReactMouseEvent<HTMLDivElement>): void => {
    event.preventDefault()
    setSplitDragging(true)
  }

  const replaceSessionSummary = (mode: EditorMode, summary: ChatSessionSummary): void => {
    setSessionSummaries((currentSummaries) => ({
      ...currentSummaries,
      [mode]: [summary, ...currentSummaries[mode].filter((item) => item.id !== summary.id)].sort(
        (a, b) => b.updatedAt - a.updatedAt
      )
    }))
  }

  const loadSessionsForMode = async (mode: EditorMode): Promise<void> => {
    if (!application.workspaceRoot) {
      setSessionSummaries((currentSummaries) => ({ ...currentSummaries, [mode]: [] }))
      setAgentMessages((currentMessages) => ({ ...currentMessages, [mode]: [] }))
      setActiveSessionIds((currentSessionIds) => ({ ...currentSessionIds, [mode]: undefined }))
      return
    }

    setSessionLoadingModes((currentLoadingModes) => ({ ...currentLoadingModes, [mode]: true }))
    setSessionErrors((currentErrors) => ({ ...currentErrors, [mode]: undefined }))
    try {
      const nextSessions = await listChatSessions(application.workspaceRoot, mode)
      setSessionSummaries((currentSummaries) => ({ ...currentSummaries, [mode]: nextSessions }))
      if (nextSessions.length === 0) {
        setAgentMessages((currentMessages) => ({ ...currentMessages, [mode]: [] }))
        setActiveSessionIds((currentSessionIds) => ({ ...currentSessionIds, [mode]: undefined }))
        agUiSessionsRef.current[mode] = undefined
        return
      }
      await openChatSession(mode, nextSessions[0].id)
    } catch (caughtError) {
      setSessionErrors((currentErrors) => ({
        ...currentErrors,
        [mode]: caughtError instanceof Error ? caughtError.message : '读取本地会话失败。'
      }))
    } finally {
      setSessionLoadingModes((currentLoadingModes) => ({ ...currentLoadingModes, [mode]: false }))
    }
  }

  const openChatSession = async (mode: EditorMode, sessionId: string): Promise<void> => {
    if (!application.workspaceRoot) return

    const session = await readChatSession(application.workspaceRoot, mode, sessionId)
    setActiveSessionIds((currentSessionIds) => ({ ...currentSessionIds, [mode]: session.id }))
    setAgentMessages((currentMessages) => ({ ...currentMessages, [mode]: session.messages }))
    setDrafts((currentDrafts) => ({ ...currentDrafts, [mode]: '' }))
    setRightPanel(undefined)
    agUiSessionsRef.current[mode] = new AgUiChatSession(session.threadId)
  }

  const handleOpenSession = async (sessionId: string): Promise<void> => {
    if (sessionId === activeSessionId || loadingSessions) return
    setSessionLoadingModes((currentLoadingModes) => ({
      ...currentLoadingModes,
      [editorMode]: true
    }))
    setSessionErrors((currentErrors) => ({ ...currentErrors, [editorMode]: undefined }))
    try {
      await openChatSession(editorMode, sessionId)
    } catch (caughtError) {
      setSessionErrors((currentErrors) => ({
        ...currentErrors,
        [editorMode]: caughtError instanceof Error ? caughtError.message : '打开本地会话失败。'
      }))
    } finally {
      setSessionLoadingModes((currentLoadingModes) => ({
        ...currentLoadingModes,
        [editorMode]: false
      }))
    }
  }

  const handleOpenSessionKeyDown = (
    event: KeyboardEvent<HTMLDivElement>,
    sessionId: string
  ): void => {
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    handleOpenSession(sessionId)
  }

  const createNewSession = async (): Promise<void> => {
    const agUiSession = new AgUiChatSession()
    agUiSessionsRef.current[editorMode] = agUiSession
    setAgentMessages((currentMessages) => ({ ...currentMessages, [editorMode]: [] }))
    setDrafts((currentDrafts) => ({ ...currentDrafts, [editorMode]: '' }))
    setRightPanel(undefined)

    if (!application.workspaceRoot) {
      setActiveSessionIds((currentSessionIds) => ({
        ...currentSessionIds,
        [editorMode]: undefined
      }))
      return
    }

    const now = Date.now()
    const session: ChatSessionRecord = {
      id: createChatSessionId(),
      title: '新对话',
      editorMode,
      threadId: agUiSession.threadId,
      workspaceRoot: application.workspaceRoot,
      messages: [],
      createdAt: now,
      updatedAt: now
    }
    setActiveSessionIds((currentSessionIds) => ({ ...currentSessionIds, [editorMode]: session.id }))
    try {
      const summary = await saveChatSession(session)
      replaceSessionSummary(editorMode, summary)
    } catch (caughtError) {
      setSessionErrors((currentErrors) => ({
        ...currentErrors,
        [editorMode]: caughtError instanceof Error ? caughtError.message : '创建本地会话失败。'
      }))
    }
  }

  const handleCreateSessionFromList = (): void => {
    if (!application.workspaceRoot) return
    createNewSession()
  }

  const handleCreateSessionKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    if (!application.workspaceRoot || (event.key !== 'Enter' && event.key !== ' ')) return
    event.preventDefault()
    createNewSession()
  }

  const handleDeleteSession = async (sessionId: string): Promise<void> => {
    if (
      !application.workspaceRoot ||
      deletingSessionId ||
      (loading && activeSessionId === sessionId)
    )
      return

    const nextSession = sessions.find((session) => session.id !== sessionId)
    setDeletingSessionIds((currentDeletingIds) => ({
      ...currentDeletingIds,
      [editorMode]: sessionId
    }))
    setSessionErrors((currentErrors) => ({ ...currentErrors, [editorMode]: undefined }))

    try {
      await deleteChatSession(application.workspaceRoot, editorMode, sessionId)
      setSessionSummaries((currentSummaries) => ({
        ...currentSummaries,
        [editorMode]: currentSummaries[editorMode].filter((session) => session.id !== sessionId)
      }))

      if (activeSessionId === sessionId) {
        if (nextSession) {
          await openChatSession(editorMode, nextSession.id)
        } else {
          setAgentMessages((currentMessages) => ({ ...currentMessages, [editorMode]: [] }))
          setActiveSessionIds((currentSessionIds) => ({
            ...currentSessionIds,
            [editorMode]: undefined
          }))
          setDrafts((currentDrafts) => ({ ...currentDrafts, [editorMode]: '' }))
          agUiSessionsRef.current[editorMode] = undefined
        }
      }

      antdMessage.success('已删除会话')
    } catch (caughtError) {
      setSessionErrors((currentErrors) => ({
        ...currentErrors,
        [editorMode]: caughtError instanceof Error ? caughtError.message : '删除本地会话失败。'
      }))
    } finally {
      setDeletingSessionIds((currentDeletingIds) => ({
        ...currentDeletingIds,
        [editorMode]: undefined
      }))
    }
  }

  const persistSession = async (
    mode: EditorMode,
    nextMessages: ChatSessionMessage[],
    options?: { titleFrom?: string; sessionId?: string; threadId?: string }
  ): Promise<void> => {
    if (!application.workspaceRoot) return
    const existingSummary = sessionSummaries[mode].find(
      (summary) => summary.id === (options?.sessionId || activeSessionIds[mode])
    )
    const now = Date.now()
    const session: ChatSessionRecord = {
      id: options?.sessionId || existingSummary?.id || createChatSessionId(),
      title:
        options?.titleFrom && (!existingSummary || existingSummary.title === '新对话')
          ? createChatSessionTitle(options.titleFrom)
          : existingSummary?.title || '新对话',
      editorMode: mode,
      threadId:
        options?.threadId ||
        existingSummary?.threadId ||
        agUiSessionsRef.current[mode]?.threadId ||
        createChatSessionId(),
      workspaceRoot: application.workspaceRoot,
      messages: nextMessages,
      createdAt: existingSummary?.createdAt || now,
      updatedAt: now
    }
    setActiveSessionIds((currentSessionIds) => ({ ...currentSessionIds, [mode]: session.id }))
    const summary = await saveChatSession(session)
    replaceSessionSummary(mode, summary)
  }

  const handleSend = async (): Promise<void> => {
    const message = draft.trim()
    if (!message || loading) return

    const userMessage: AgentChatMessage = {
      id: Date.now(),
      role: 'user',
      content: message,
      createdAt: Date.now()
    }
    const agUiSession =
      agUiSessionsRef.current[editorMode] ??
      (agUiSessionsRef.current[editorMode] = new AgUiChatSession())
    const sessionId = activeSessionId || createChatSessionId()
    const nextMessages = [...messages, userMessage]

    setAgentMessages((currentMessages) => ({
      ...currentMessages,
      [editorMode]: nextMessages
    }))
    setDrafts((currentDrafts) => ({ ...currentDrafts, [editorMode]: '' }))
    setErrors((currentErrors) => ({ ...currentErrors, [editorMode]: undefined }))
    setLoadingModes((currentLoadingModes) => ({ ...currentLoadingModes, [editorMode]: true }))

    try {
      await persistSession(editorMode, nextMessages, {
        sessionId,
        threadId: agUiSession.threadId,
        titleFrom: message
      })
      const { answer: rawAnswer, workflow } = await agUiSession.sendMessage(message, {
        workspaceRoot: application.workspaceRoot,
        application
      })
      const answer = rawAnswer.trim()
      const assistantMessage: AgentChatMessage = {
        id: Date.now() + 1,
        role: 'assistant',
        content: answer || 'Workflow 已返回，但内容为空。',
        workflow,
        createdAt: Date.now()
      }
      const completedMessages = [...nextMessages, assistantMessage]

      setAgentMessages((currentMessages) => ({
        ...currentMessages,
        [editorMode]: completedMessages
      }))
      await persistSession(editorMode, completedMessages, {
        sessionId,
        threadId: agUiSession.threadId,
        titleFrom: message
      })
      publishAiMessage(editorMode, answer)
    } catch (caughtError) {
      const message = caughtError instanceof Error ? caughtError.message : '调用 Workflow 失败。'
      setErrors((currentErrors) => ({ ...currentErrors, [editorMode]: message }))
    } finally {
      setLoadingModes((currentLoadingModes) => ({
        ...currentLoadingModes,
        [editorMode]: false
      }))
    }
  }

  return (
    <section
      className={cx(
        'ai-chat-panel',
        rightPanelOpen && 'embedded-preview-open',
        splitDragging && 'split-dragging'
      )}
      ref={panelRef}
      style={panelStyle}
    >
      <div className={cx('ai-chat-assistant')}>
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
              onClick={handleCreateSessionFromList}
              onKeyDown={handleCreateSessionKeyDown}
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
              sessions.map((session) => (
                <div
                  className={cx('session-item', activeSessionId === session.id && 'active')}
                  key={session.id}
                >
                  <div
                    className={cx('session-item-content')}
                    onClick={() => handleOpenSession(session.id)}
                    onKeyDown={(event) => handleOpenSessionKeyDown(event, session.id)}
                    tabIndex={0}
                  >
                    <span className={cx('session-item-title')}>
                      <MessageOutlined /> {session.title}
                    </span>
                    <span className={cx('session-item-meta')}>
                      {formatSessionTime(session.updatedAt)} · {session.messageCount} 条
                    </span>
                  </div>
                  <Popconfirm
                    cancelText="取消"
                    disabled={loading && activeSessionId === session.id}
                    okText="删除"
                    okButtonProps={{ danger: true }}
                    onCancel={(event) => event?.stopPropagation()}
                    onConfirm={(event) => {
                      event?.stopPropagation()
                      return handleDeleteSession(session.id)
                    }}
                    title="删除这个历史会话？"
                  >
                    <Button
                      aria-label={`删除会话 ${session.title}`}
                      className={cx('session-delete-button')}
                      danger
                      disabled={loadingSessions || (loading && activeSessionId === session.id)}
                      icon={<DeleteOutlined />}
                      loading={deletingSessionId === session.id}
                      onClick={(event) => event.stopPropagation()}
                      size="small"
                      title="删除会话"
                      type="text"
                    />
                  </Popconfirm>
                </div>
              ))
            )}
          </div>
          {sessionError && <Alert message={sessionError} showIcon type="error" />}
        </aside>

        <div className={cx('ai-chat-main')}>
          {showPreviewActions && (
            <div className={cx('preview-actions')}>
              {embeddedPreviewOpen && (
                <Button
                  aria-label="关闭内嵌预览"
                  icon={<CloseOutlined />}
                  onClick={() => setRightPanel(undefined)}
                  type="text"
                />
              )}
              <Dropdown
                menu={{ items: previewMenuItems, onClick: handlePreviewAction }}
                trigger={['click']}
              >
                <Button icon={<DesktopOutlined />} type="primary">
                  预览应用 <DownOutlined />
                </Button>
              </Dropdown>
            </div>
          )}
          <header className={cx('ai-chat-header')}>
            <div className={cx('ai-chat-title')}>
              <Text className={cx('editor-scope-tag', editorMode)}>WORKFLOW</Text>
              <Title level={4}>{copy.title}</Title>
              <Text type="secondary">{copy.description}</Text>
            </div>
          </header>

          {previewError && (
            <Alert
              className={cx('preview-action-error')}
              message={previewError}
              showIcon
              type="error"
            />
          )}

          <div className={cx('ai-message-list')} aria-live="polite">
            {messages.length === 0 ? (
              <Empty description={copy.empty} image={Empty.PRESENTED_IMAGE_SIMPLE} />
            ) : (
              messages.map((message) => (
                <article className={cx('ai-message', message.role)} key={message.id}>
                  <Text className={cx('ai-message-label')}>
                    {message.role === 'user' ? (
                      <>
                        <UserOutlined /> 用户输入
                      </>
                    ) : (
                      <>
                        <RobotOutlined /> {copy.label}
                      </>
                    )}
                  </Text>
                  {message.role === 'assistant' ? (
                    <>
                      <MarkdownContent content={message.content} />
                      {message.workflow && <WorkflowRunCard workflow={message.workflow} />}
                    </>
                  ) : (
                    <Text className={cx('ai-message-text')}>{message.content}</Text>
                  )}
                </article>
              ))
            )}
            {loading && (
              <div className={cx('ai-message', 'assistant', 'loading')}>
                <Spin size="small" />
                <Text type="secondary">正在运行 Workflow...</Text>
              </div>
            )}
          </div>

          <div className={cx('ai-chat-composer')}>
            {error && <Alert message={error} showIcon type="error" />}
            <TextArea
              aria-label={`${copy.title}输出内容`}
              autoSize={{ minRows: 3, maxRows: 6 }}
              placeholder={copy.placeholder}
              value={draft}
              onChange={(event) =>
                setDrafts((currentDrafts) => ({
                  ...currentDrafts,
                  [editorMode]: event.target.value
                }))
              }
              onPressEnter={(event) => {
                if (!event.shiftKey) {
                  event.preventDefault()
                  handleSend()
                }
              }}
            />
            <div className={cx('ai-chat-composer-footer')}>
              <Text className={cx('workspace-root-label')} title={workspaceRoot}>
                <FolderOpenOutlined /> 工作目录：{workspaceRoot}
              </Text>
              <Button
                disabled={!draft.trim() || loading}
                icon={<SendOutlined />}
                loading={loading}
                onClick={handleSend}
                type="primary"
              >
                发送给 Workflow
              </Button>
            </div>
          </div>
        </div>
      </div>

      {rightPanelOpen && (
        <div
          aria-label="拖动调整右侧面板宽度"
          aria-orientation="vertical"
          className={cx('panel-split-handle', splitDragging && 'dragging')}
          onMouseDown={handlePanelSplitDragStart}
          role="separator"
          title="拖动调整左右面板宽度"
        >
          <HolderOutlined className={cx('panel-split-handle-icon')} />
        </div>
      )}

      {rightPanel?.type === 'preview' && (
        <div className={cx('embedded-preview-pane')}>
          <BrowserPreviewPanel application={application} />
        </div>
      )}
    </section>
  )
}

function WorkflowRunCard({ workflow }: { workflow: WorkflowRunPayload }): ReactElement {
  const status = String(workflow.summary.status || 'unknown')
  const artifacts = workflow.summary.artifacts || {}
  const recentEvents = workflow.events.slice(-8)

  return (
    <div className={cx('workflow-run-card')}>
      <div className={cx('workflow-run-header')}>
        <Text strong>Workflow Run</Text>
        <Tag color={workflowStatusColor(status)}>{status}</Tag>
      </div>
      {workflow.summary.message && <Text>{String(workflow.summary.message)}</Text>}
      {Object.keys(artifacts).length > 0 && (
        <div className={cx('workflow-artifacts')}>
          <Text type="secondary">产物</Text>
          {Object.entries(artifacts).map(([name, path]) => (
            <Text code key={name}>
              {name}: {path}
            </Text>
          ))}
        </div>
      )}
      {recentEvents.length > 0 && (
        <div className={cx('workflow-events')}>
          <Text type="secondary">最近事件</Text>
          {recentEvents.map((event, index) => (
            <div className={cx('workflow-event')} key={`${event.type}-${event.timestamp}-${index}`}>
              <Tag>{event.nodeName || event.type}</Tag>
              <Text>{event.message || event.status || event.type}</Text>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function workflowStatusColor(status: string): string {
  if (status === 'completed' || status === 'passed') return 'green'
  if (status === 'failed' || status === 'error') return 'red'
  if (status === 'running') return 'blue'
  return 'default'
}

function formatSessionTime(value: number): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '未知时间'
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  })
}

function clampAssistantPanelWidth(nextWidth: number, panel: HTMLElement | null): number {
  const panelWidth = panel?.getBoundingClientRect().width ?? 0
  const maxWidth = Math.max(
    MIN_ASSISTANT_PANEL_WIDTH,
    panelWidth - MIN_RIGHT_PANEL_WIDTH - SPLIT_HANDLE_WIDTH
  )

  return Math.min(Math.max(nextWidth, MIN_ASSISTANT_PANEL_WIDTH), maxWidth)
}
