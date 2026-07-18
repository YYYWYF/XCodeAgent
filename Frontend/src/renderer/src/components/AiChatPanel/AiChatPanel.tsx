import { HolderOutlined } from '@ant-design/icons'
import { Alert } from 'antd'
import type { ReactElement } from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useWorkbench } from '../../context'
import type {
  ApplicationConfig,
  DevelopmentPlanningPageOption,
  ApplicationMenuItem,
  EditorMode,
  WorkspaceCodeChangeSet
} from '../../typings'
import { cx, getInitialPreviewUrl, openPreviewWindow } from '../../utils'
import BrowserPreviewPanel from '../BrowserPreviewPanel/BrowserPreviewPanel'
import ChatComposer from './components/ChatComposer'
import ChatHeader from './components/ChatHeader'
import CodeDiffDetailPanel from './components/CodeDiffDetailPanel'
import MessageList from './components/MessageList'
import PageContextHeader from './components/PageContextHeader'
import PreviewActions from './components/PreviewActions'
import SessionSidebar from './components/SessionSidebar'
import SessionHistoryDropdown from './components/SessionHistoryDropdown'
import AgentFilesPage from '../AgentFilesPage/AgentFilesPage'
import DetailConfirmationPageSelector from '../DetailConfirmationPageSelector'
import SettingsPage from '../SettingsPage/SettingsPage'
import SkillsPage from '../SkillsPage/SkillsPage'
import { useAssistantPreviewLayout } from './hooks/useAssistantPreviewLayout'
import { useChatSessions } from './hooks/useChatSessions'
import { useCodeChangeRevert } from './hooks/useCodeChangeRevert'
import { useWorkflowConversation } from './hooks/useWorkflowConversation'
import type { SessionIdentity } from './hooks/sessionRuntime'
import { chatCopy } from './constants'
import './AiChatPanel.less'

type Props = {
  application: ApplicationConfig
  developmentPlanningReady: boolean
  developmentPlanningPages: DevelopmentPlanningPageOption[]
  editorMode: EditorMode
  onApplicationUpdate: (application: ApplicationConfig) => void
  onReturnWelcome: () => void
  onThemeChange: (theme: 'light' | 'dark') => void
  theme: 'light' | 'dark'
}

type ActiveView = 'chat' | 'skills' | 'files' | 'settings'

/** 按页面名称递归查找对应的菜单配置。 */
function findPageMenuItem(
  items: ApplicationMenuItem[],
  label: string
): ApplicationMenuItem | undefined {
  for (const item of items) {
    if (item.label === label) return item
    const matchedChild = findPageMenuItem(item.children || [], label)
    if (matchedChild) return matchedChild
  }
  return undefined
}

/** 组织应用侧栏、对话区、页面信息与预览面板的主工作台。 */
export default function AiChatPanel({
  application,
  developmentPlanningReady,
  developmentPlanningPages,
  editorMode,
  onApplicationUpdate,
  onReturnWelcome,
  onThemeChange,
  theme
}: Props): ReactElement {
  const [activeView, setActiveView] = useState<ActiveView>('chat')
  const [activePageId, setActivePageId] = useState('')
  const [previewError, setPreviewError] = useState('')
  const [locallyDesignedPageIds, setLocallyDesignedPageIds] = useState<Set<string>>(
    () => new Set()
  )
  const runningSessionsRef = useRef<Map<string, SessionIdentity>>(new Map())
  const { publishAiMessage } = useWorkbench()
  const {
    assistantPanelWidth,
    embeddedPreviewOpen,
    handlePanelSplitKeyDown,
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
    selectedSkills,
    sessionError,
    sessions,
    setDraftByKey,
    setSelectedSkillsByKey,
    setSessionMessages
  } = useChatSessions({
    application,
    editorMode,
    onCloseRightPanel: () => setRightPanel(undefined),
    runningSessionsRef
  })

  const {
    activeWorkflow,
    error,
    handleSend,
    handleStartDetailConfirmation,
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
    selectedSkills,
    setDraftByKey,
    setSelectedSkillsByKey,
    setSessionMessages
  })
  const { requestCodeChangeRevert, revertingCodeChangeIds } = useCodeChangeRevert({
    activeSession,
    disabled: loading || workspaceBusy,
    getSessionMessages,
    persistSession,
    rightPanel,
    setRightPanel,
    setSessionMessages
  })

  const copy = chatCopy[editorMode]
  const workspaceRoot = application.workspaceRoot || '未选择工作目录'
  const showPreviewActions = editorMode === 'frontend'
  const activePageOption = useMemo(
    () => developmentPlanningPages.find((page) => page.key === activePageId),
    [activePageId, developmentPlanningPages]
  )
  const activePageTitle = activePageOption?.label
    || application.defaultPage
    || application.pages[0]
    || '页面'
  const activePage = useMemo(
    () => findPageMenuItem(application.menus.items, activePageTitle),
    [activePageTitle, application.menus.items]
  )
  const hasDesignedPage = developmentPlanningPages.some((page) => page.designed)
  const initialPageSelectionRequired = !developmentPlanningReady
    || (!hasDesignedPage && locallyDesignedPageIds.size === 0)
  const activePageDesigned = Boolean(
    activePageOption?.designed
      || (activePageOption && locallyDesignedPageIds.has(activePageOption.key))
  )
  const activeSessionUpdatedAt = sessions.find(
    (session) => session.id === activeSessionId
  )?.updatedAt

  // 页面目录加载后优先定位首个已设计页面，避免再次显示首次选择界面。
  useEffect(() => {
    setActivePageId((currentPageId) => {
      if (developmentPlanningPages.some((page) => page.key === currentPageId)) {
        return currentPageId
      }
      return developmentPlanningPages.find((page) => page.designed)?.key
        || developmentPlanningPages[0]?.key
        || ''
    })
  }, [developmentPlanningPages])

  // 切换工作区时清除仅用于当前运行周期的设计完成标记。
  useEffect(() => {
    setLocallyDesignedPageIds(new Set())
  }, [application.workspaceRoot])

  /** 在右侧工作区打开当前页面预览。 */
  const handleOpenPage = (): void => {
    setRightPanel({ type: 'preview' })
  }
  const handleOpenFullscreenPreview = async (): Promise<void> => {
    setPreviewError('')

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

  const handleShowSkills = (): void => {
    setPreviewError('')
    setRightPanel(undefined)
    setActiveView('skills')
  }

  const handleShowFiles = (): void => {
    setPreviewError('')
    setRightPanel(undefined)
    setActiveView('files')
  }

  const handleShowSettings = (): void => {
    setPreviewError('')
    setRightPanel(undefined)
    setActiveView('settings')
  }

  const handleCreateChatSession = (): void => {
    setActiveView('chat')
    handleCreateSessionFromList()
  }

  /** 从应用大纲切换页面，并确保页面状态在对话区域呈现。 */
  const handlePageSelect = (page: DevelopmentPlanningPageOption): void => {
    setPreviewError('')
    setRightPanel(undefined)
    setActiveView('chat')
    setActivePageId(page.key)
  }

  /** 启动当前页面的详细设计，并在本次工作台会话中解锁其对话区域。 */
  const handleStartPageDesign = async (
    pageId: string,
    pageLabel: string,
    hasDetailPlan: boolean
  ): Promise<void> => {
    setActivePageId(pageId)
    const started = await handleStartDetailConfirmation(pageId, pageLabel, hasDetailPlan)
    if (started) {
      setLocallyDesignedPageIds((current) => new Set(current).add(pageId))
    }
  }

  const handleOpenChatSession = async (sessionId: string): Promise<void> => {
    setActiveView('chat')
    await handleOpenSession(sessionId)
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
          outlineLocked={initialPageSelectionRequired}
          onCreateSession={handleCreateChatSession}
          onDeleteSession={handleDeleteSession}
          onOpenSession={handleOpenChatSession}
          onOpenSessionKeyDown={(event, sessionId) => {
            if (event.key === 'Enter' || event.key === ' ') setActiveView('chat')
            handleOpenSessionKeyDown(event, sessionId)
          }}
          onPageSelect={handlePageSelect}
          onReturnWelcome={onReturnWelcome}
          onShowFiles={handleShowFiles}
          onShowSettings={handleShowSettings}
          onShowSkills={handleShowSkills}
          pages={developmentPlanningPages}
          selectedPageId={activePageId}
          filesActive={activeView === 'files'}
          sessionError={sessionError}
          sessionRunStates={sessionRunStates}
          sessions={sessions}
          settingsActive={activeView === 'settings'}
          skillsActive={activeView === 'skills'}
          workspaceRoot={workspaceRoot}
        />

        {activeView === 'skills' ? (
          <SkillsPage onThemeChange={onThemeChange} theme={theme} />
        ) : activeView === 'files' ? (
          <AgentFilesPage />
        ) : activeView === 'settings' ? (
          <SettingsPage application={application} onSaved={onApplicationUpdate} />
        ) : initialPageSelectionRequired ? (
          <div className={cx('ai-chat-main')}>
            <DetailConfirmationPageSelector
              disabled={loading || workspaceBusy}
              loading={!developmentPlanningReady}
              onStart={handleStartPageDesign}
              pages={developmentPlanningPages}
            />
          </div>
        ) : (
          <div className={cx('ai-chat-main')}>
            <ChatHeader
              actions={
                <>
                  {showPreviewActions ? (
                    <PreviewActions
                      embeddedPreviewOpen={embeddedPreviewOpen}
                      onOpenFullscreenPreview={handleOpenFullscreenPreview}
                      onToggleEmbeddedPreview={() =>
                        setRightPanel(embeddedPreviewOpen ? undefined : { type: 'preview' })
                      }
                    />
                  ) : null}
                  <SessionHistoryDropdown
                    activeSessionId={activeSessionId}
                    deletingSessionId={deletingSessionId}
                    loadingSessions={loadingSessions}
                    onCreateSession={handleCreateChatSession}
                    onDeleteSession={handleDeleteSession}
                    onOpenSession={handleOpenChatSession}
                    onOpenSessionKeyDown={handleOpenSessionKeyDown}
                    sessionError={sessionError}
                    sessionRunStates={sessionRunStates}
                    sessions={sessions}
                    theme={theme}
                    workspaceSelected={Boolean(application.workspaceRoot)}
                  />
                </>
              }
              onThemeChange={onThemeChange}
              pageTitle={activePageTitle}
              theme={theme}
            />

            <PageContextHeader
              description={activePageOption?.purpose || activePage?.purpose || application.senario || '当前应用页面'}
              keyFeatures={activePage?.keyFeatures || []}
              lastAnalyzedAt={activeSessionUpdatedAt}
              onOpenFullscreenPage={handleOpenFullscreenPreview}
              onOpenPage={handleOpenPage}
              pagePath={activePageOption?.path || activePage?.path || '/'}
              pageTitle={activePageTitle}
              previewAvailable={showPreviewActions}
              theme={theme}
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
              codeChangeActionsDisabled={loading || workspaceBusy}
              copy={copy}
              loading={loading}
              messages={messages}
              onOpenCodeChangeFile={handleOpenCodeChangeFile}
              onRevertCodeChanges={requestCodeChangeRevert}
              onSubmitClarification={handleSubmitClarification}
              revertingCodeChangeIds={revertingCodeChangeIds}
            />

            <ChatComposer
              activeWorkflow={activeWorkflow}
              copy={copy}
              draft={draft}
              error={error}
              loading={loading}
              onDraftChange={(value) => setDraftByKey(draftKey, value)}
              onSelectedSkillsChange={(value) => setSelectedSkillsByKey(draftKey, value)}
              onSend={handleSend}
              onStopGenerating={handleStopGenerating}
              stopping={stopping}
              selectedSkills={selectedSkills}
              workspaceBusy={workspaceBusy}
              workspaceRoot={workspaceRoot}
            />

            {activePageOption && !activePageDesigned ? (
              <DetailConfirmationPageSelector
                disabled={loading || workspaceBusy}
                loading={false}
                mode="locked"
                onStart={handleStartPageDesign}
                pages={developmentPlanningPages}
                selectedPage={activePageOption}
              />
            ) : null}
          </div>
        )}
      </div>

      {rightPanelOpen && (
        <div
          aria-label="拖动调整右侧面板宽度"
          aria-orientation="vertical"
          aria-valuenow={assistantPanelWidth}
          className={cx('panel-split-handle', splitDragging && 'dragging')}
          onKeyDown={handlePanelSplitKeyDown}
          onMouseDown={handlePanelSplitDragStart}
          role="separator"
          tabIndex={0}
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
