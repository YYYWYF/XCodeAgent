import { HolderOutlined } from '@ant-design/icons'
import { Alert } from 'antd'
import type { MenuProps } from 'antd'
import type { ReactElement } from 'react'
import { useRef, useState } from 'react'
import { useWorkbench } from '../../context'
import type { ApplicationConfig, EditorMode, WorkspaceCodeChangeSet } from '../../typings'
import { cx, getInitialPreviewUrl, openPreviewWindow } from '../../utils'
import BrowserPreviewPanel from '../BrowserPreviewPanel/BrowserPreviewPanel'
import ChatComposer from './components/ChatComposer'
import ChatHeader from './components/ChatHeader'
import CodeDiffDetailPanel from './components/CodeDiffDetailPanel'
import MessageList from './components/MessageList'
import PreviewActions from './components/PreviewActions'
import SessionSidebar from './components/SessionSidebar'
import { useAssistantPreviewLayout } from './hooks/useAssistantPreviewLayout'
import { useChatSessions } from './hooks/useChatSessions'
import { useWorkflowConversation } from './hooks/useWorkflowConversation'
import type { SessionIdentity } from './hooks/sessionRuntime'
import { chatCopy } from './constants'
import './AiChatPanel.less'

type Props = {
  application: ApplicationConfig
  editorMode: EditorMode
  onReturnWelcome: () => void
  onThemeChange: (theme: 'light' | 'dark') => void
  theme: 'light' | 'dark'
}

export default function AiChatPanel({
  application,
  editorMode,
  onReturnWelcome,
  onThemeChange,
  theme
}: Props): ReactElement {
  const [previewError, setPreviewError] = useState('')
  const runningSessionsRef = useRef<Map<string, SessionIdentity>>(new Map())
  const { publishAiMessage } = useWorkbench()
  const {
    embeddedPreviewOpen,
    handlePanelSplitDragStart,
    panelRef,
    panelStyle,
    rightPanel,
    rightPanelOpen,
    setRightPanel,
    splitDragging
  } = useAssistantPreviewLayout()

  const {
    activeSession,
    activeSessionId,
    agUiSessionsRef,
    deletingSessionId,
    draft,
    draftKey,
    ensureActiveSession,
    getSessionMessages,
    handleCreateSessionFromList,
    handleDeleteSession,
    handleOpenSession,
    handleOpenSessionKeyDown,
    loadingSessions,
    messages,
    persistSession,
    sessionError,
    sessions,
    setDraftByKey,
    setSessionMessages
  } = useChatSessions({
    application,
    editorMode,
    onCloseRightPanel: () => setRightPanel(undefined),
    runningSessionsRef
  })

  const {
    error,
    handleSend,
    handleStopGenerating,
    handleSubmitClarification,
    loading,
    sessionRunStates,
    stopping,
    workspaceBusy
  } = useWorkflowConversation({
    activeSession,
    agUiSessionsRef,
    application,
    draft,
    draftKey,
    editorMode,
    ensureActiveSession,
    getSessionMessages,
    persistSession,
    publishAiMessage,
    runningSessionsRef,
    setDraftByKey,
    setSessionMessages
  })

  const copy = chatCopy[editorMode]
  const workspaceRoot = application.workspaceRoot || '未选择工作目录'
  const showPreviewActions = editorMode === 'frontend'
  const activeSessionTitle = sessions.find((session) => session.id === activeSessionId)?.title

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

  const handleOpenCodeChangeFile = (
    codeChanges: WorkspaceCodeChangeSet,
    selectedPath: string
  ): void => {
    setRightPanel({ type: 'diff', codeChanges, selectedPath })
  }

  return (
    <section
      className={cx(
        'ai-chat-panel',
        rightPanelOpen && 'embedded-preview-open',
        rightPanel?.type === 'diff' && 'diff-panel-open',
        splitDragging && 'split-dragging'
      )}
      ref={panelRef}
      style={panelStyle}
    >
      <div className={cx('ai-chat-assistant')}>
        <SessionSidebar
          activeSessionId={activeSessionId}
          application={application}
          deletingSessionId={deletingSessionId}
          loadingSessions={loadingSessions}
          onCreateSession={handleCreateSessionFromList}
          onDeleteSession={handleDeleteSession}
          onOpenSession={handleOpenSession}
          onOpenSessionKeyDown={handleOpenSessionKeyDown}
          onReturnWelcome={onReturnWelcome}
          sessionError={sessionError}
          sessionRunStates={sessionRunStates}
          sessions={sessions}
          workspaceRoot={workspaceRoot}
        />

        <div className={cx('ai-chat-main')}>
          <ChatHeader
            actions={
              showPreviewActions ? (
                <PreviewActions
                  embeddedPreviewOpen={embeddedPreviewOpen}
                  onCloseEmbeddedPreview={() => setRightPanel(undefined)}
                  onPreviewAction={handlePreviewAction}
                  theme={theme}
                />
              ) : undefined
            }
            copy={copy}
            editorMode={editorMode}
            onThemeChange={onThemeChange}
            theme={theme}
            title={activeSessionTitle || '新对话'}
            workspaceName={application.name}
          />

          {previewError && (
            <Alert
              className={cx('preview-action-error')}
              message={previewError}
              showIcon
              type="error"
            />
          )}

          <MessageList
            copy={copy}
            loading={loading}
            messages={messages}
            onOpenCodeChangeFile={handleOpenCodeChangeFile}
            onSubmitClarification={handleSubmitClarification}
          />

          <ChatComposer
            copy={copy}
            draft={draft}
            error={error}
            loading={loading}
            onDraftChange={(value) => setDraftByKey(draftKey, value)}
            onSend={handleSend}
            onStopGenerating={handleStopGenerating}
            stopping={stopping}
            workspaceBusy={workspaceBusy}
            workspaceRoot={workspaceRoot}
          />
        </div>
      </div>

      {rightPanel?.type === 'preview' && (
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

      {rightPanel?.type === 'diff' && (
        <div className={cx('embedded-preview-pane', 'diff-detail-pane')}>
          <CodeDiffDetailPanel
            codeChanges={rightPanel.codeChanges}
            selectedPath={rightPanel.selectedPath}
            onClose={() => setRightPanel(undefined)}
          />
        </div>
      )}
    </section>
  )
}
