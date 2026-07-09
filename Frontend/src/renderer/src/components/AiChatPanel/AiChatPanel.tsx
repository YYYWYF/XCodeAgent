import { HolderOutlined } from '@ant-design/icons'
import { Alert } from 'antd'
import type { MenuProps } from 'antd'
import type { ReactElement } from 'react'
import { useEffect, useRef, useState } from 'react'
import { useWorkbench } from '../../context'
import type { ApplicationConfig, EditorMode } from '../../typings'
import { cx, getInitialPreviewUrl, openPreviewWindow } from '../../utils'
import BrowserPreviewPanel from '../BrowserPreviewPanel/BrowserPreviewPanel'
import ChatComposer from './components/ChatComposer'
import ChatHeader from './components/ChatHeader'
import MessageList from './components/MessageList'
import PreviewActions from './components/PreviewActions'
import SessionSidebar from './components/SessionSidebar'
import { useAssistantPreviewLayout } from './hooks/useAssistantPreviewLayout'
import { useChatSessions } from './hooks/useChatSessions'
import { useWorkflowConversation } from './hooks/useWorkflowConversation'
import { chatCopy } from './constants'
import './AiChatPanel.less'

type Props = {
  application: ApplicationConfig
  editorMode: EditorMode
  onReturnWelcome: () => void
}

export default function AiChatPanel({
  application,
  editorMode,
  onReturnWelcome
}: Props): ReactElement {
  const [previewError, setPreviewError] = useState('')
  const workflowLoadingRef = useRef(false)
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
    activeSessionId,
    agUiSessionsRef,
    deletingSessionId,
    draft,
    handleCreateSessionFromList,
    handleCreateSessionKeyDown,
    handleDeleteSession,
    handleOpenSession,
    handleOpenSessionKeyDown,
    loadingSessions,
    messages,
    persistSession,
    sessionError,
    sessions,
    setAgentMessages,
    setDraftForMode
  } = useChatSessions({
    application,
    editorMode,
    loadingRef: workflowLoadingRef,
    onCloseRightPanel: () => setRightPanel(undefined)
  })

  const { error, handleSend, handleStopGenerating, handleSubmitClarification, loading, stopping } =
    useWorkflowConversation({
      activeSessionId,
      agUiSessionsRef,
      application,
      draft,
      editorMode,
      messages,
      persistSession,
      publishAiMessage,
      setAgentMessages,
      setDraftForMode
    })

  useEffect(() => {
    workflowLoadingRef.current = loading
  }, [loading, workflowLoadingRef])

  const copy = chatCopy[editorMode]
  const workspaceRoot = application.workspaceRoot || '未选择工作目录'
  const showPreviewActions = editorMode === 'frontend'

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
        <SessionSidebar
          activeSessionId={activeSessionId}
          application={application}
          deletingSessionId={deletingSessionId}
          loading={loading}
          loadingSessions={loadingSessions}
          onCreateSession={handleCreateSessionFromList}
          onCreateSessionKeyDown={handleCreateSessionKeyDown}
          onDeleteSession={handleDeleteSession}
          onOpenSession={handleOpenSession}
          onOpenSessionKeyDown={handleOpenSessionKeyDown}
          onReturnWelcome={onReturnWelcome}
          sessionError={sessionError}
          sessions={sessions}
          workspaceRoot={workspaceRoot}
        />

        <div className={cx('ai-chat-main')}>
          {showPreviewActions && (
            <PreviewActions
              embeddedPreviewOpen={embeddedPreviewOpen}
              onCloseEmbeddedPreview={() => setRightPanel(undefined)}
              onPreviewAction={handlePreviewAction}
            />
          )}
          <ChatHeader copy={copy} editorMode={editorMode} />

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
            onSubmitClarification={handleSubmitClarification}
          />

          <ChatComposer
            copy={copy}
            draft={draft}
            error={error}
            loading={loading}
            onDraftChange={(value) => setDraftForMode(editorMode, value)}
            onSend={handleSend}
            onStopGenerating={handleStopGenerating}
            stopping={stopping}
            workspaceRoot={workspaceRoot}
          />
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
