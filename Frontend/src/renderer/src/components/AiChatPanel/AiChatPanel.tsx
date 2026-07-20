import { HolderOutlined } from '@ant-design/icons'
import { Alert } from 'antd'
import type { ReactElement } from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useWorkbench } from '../../context'
import type {
  ApplicationConfig,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningPageOption,
  ApplicationMenuItem,
  EditorMode,
  WorkspaceCodeChangeSet
} from '../../typings'
import { cx, getInitialPreviewUrl, openPreviewWindow, storePreviewUrl } from '../../utils'
import BrowserPreviewPanel from '../BrowserPreviewPanel/BrowserPreviewPanel'
import ChatComposer from './components/ChatComposer'
import ChatHeader from './components/ChatHeader'
import CodeDiffDetailPanel from './components/CodeDiffDetailPanel'
import MessageList from './components/MessageList'
import PageContextHeader from './components/PageContextHeader'
import PreviewActions from './components/PreviewActions'
import SessionSidebar from './components/SessionSidebar'
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
import type { WorkflowPreviewTarget } from './utils'
import './AiChatPanel.less'

type Props = {
  application: ApplicationConfig
  developmentPlanningReady: boolean
  hasPageDesigns: boolean
  developmentPlanningPages: DevelopmentPlanningPageOption[]
  developmentPlanningApiContracts: DevelopmentPlanningApiContract[]
  editorMode: EditorMode
  onApplicationUpdate: (application: ApplicationConfig) => void
  onPlanningArtifactsRefresh: () => void
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

/** 在最新 ProjectPlan 页面目录中解析会话保存的页面标识，避免旧 pageId 覆盖当前选择。 */
function resolvePlanningPageId(
  pages: DevelopmentPlanningPageOption[],
  pageId: string
): string {
  const normalizedPageId = pageId.trim()
  if (!normalizedPageId) return ''
  const matched = pages.find((page) => page.pageId === normalizedPageId)
  if (matched) return matched.pageId
  const alias = pageIdAlias(normalizedPageId)
  return pages.find((page) => pageIdAlias(page.pageId) === alias)?.pageId || ''
}

/** 生成页面标识的宽松别名，兼容历史会话里的 page- 前缀差异。 */
function pageIdAlias(value: string): string {
  return value.trim().toLowerCase().replace(/_/g, '-').replace(/^page-/, '')
}

/** 组织应用侧栏、对话区、页面信息与预览面板的主工作台。 */
export default function AiChatPanel({
  application,
  developmentPlanningReady,
  hasPageDesigns,
  developmentPlanningPages,
  developmentPlanningApiContracts,
  editorMode,
  onApplicationUpdate,
  onPlanningArtifactsRefresh,
  onReturnWelcome,
  onThemeChange,
  theme
}: Props): ReactElement {
  const [activeView, setActiveView] = useState<ActiveView>('chat')
  const [activePageId, setActivePageId] = useState('')
  const [previewError, setPreviewError] = useState('')
  const runningSessionsRef = useRef<Map<string, SessionIdentity>>(new Map())
  const handledPreviewTargetRef = useRef('')
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
  const activePageOption = useMemo(
    () => developmentPlanningPages.find((page) => page.pageId === activePageId),
    [activePageId, developmentPlanningPages]
  )

  /** 接收实时 launch 结果并复用手动预览入口打开右侧面板。 */
  const handlePreviewReady = useCallback(
    (target: WorkflowPreviewTarget) => {
      if (handledPreviewTargetRef.current === target.key) return
      handledPreviewTargetRef.current = target.key
      setPreviewError('')
      storePreviewUrl(application.id, target.url)
      setRightPanel({ type: 'preview', requestKey: target.key, url: target.url })
    },
    [application.id, setRightPanel]
  )

  const {
    activeSession,
    activeSessionId,
    agUiSessionsRef,
    createPageSession,
    deletingSessionId,
    draft,
    draftKey,
    ensureActiveSession,
    ensurePageSession,
    getSessionMessages,
    handleCreateSessionFromList,
    handleDeleteSession,
    handleOpenSession,
    handleSelectPage,
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
    ensurePageSession,
    getSessionMessages,
    persistSession,
    onPreviewReady: handlePreviewReady,
    publishAiMessage,
    runningSessionsRef,
    selectedSkills,
    selectedPageId: activePageOption?.pageId || activePageOption?.key,
    selectedPageLabel: activePageOption?.label,
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
  const activePageTitle =
    activePageOption?.label || application.defaultPage || application.pages[0] || '页面'
  const activePage = useMemo(
    () => findPageMenuItem(application.menus.items, activePageTitle),
    [activePageTitle, application.menus.items]
  )
  const initialPageSelectionRequired = developmentPlanningReady && !hasPageDesigns
  const activePageDesigned = Boolean(activePageOption?.designed)
  const activeSessionUpdatedAt = sessions.find(
    (session) => session.id === activeSessionId
  )?.updatedAt

  // 页面目录刷新时保留当前页面上下文；仅在清单稳定且当前页面失效时回退。
  useEffect(() => {
    setActivePageId((currentPageId) => {
      if (developmentPlanningPages.length === 0) return currentPageId
      if (developmentPlanningPages.some((page) => page.pageId === currentPageId)) {
        return currentPageId
      }
      return (
        developmentPlanningPages.find((page) => page.designed)?.pageId ||
        developmentPlanningPages[0]?.pageId ||
        ''
      )
    })
  }, [developmentPlanningPages])

  // 打开历史页面会话时同步页面上下文，避免标题与消息归属不一致。
  useEffect(() => {
    const sessionPageId = sessions.find((session) => session.id === activeSessionId)?.pageId
    if (!sessionPageId) return
    const resolvedPageId = resolvePlanningPageId(developmentPlanningPages, sessionPageId)
    if (resolvedPageId) setActivePageId(resolvedPageId)
  }, [activeSessionId, developmentPlanningPages, sessions])

  /** 在右侧工作区打开当前页面预览。 */
  const handleOpenPage = (): void => {
    setRightPanel({ type: 'preview' })
  }

  /** 使用最近一次成功启动地址打开独立全屏预览窗口。 */
  const handleOpenFullscreenPreview = async (): Promise<void> => {
    setPreviewError('')

    try {
      const targetUrl =
        rightPanel?.type === 'preview' && rightPanel.url
          ? rightPanel.url
          : getInitialPreviewUrl(application.id)
      await openPreviewWindow(targetUrl)
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

  /** 在指定页面下新建独立会话，并立即切换到该页面。 */
  const handleCreatePageSession = async (pageId: string, pageLabel: string): Promise<void> => {
    setPreviewError('')
    setRightPanel(undefined)
    setActiveView('chat')
    setActivePageId(pageId)
    await createPageSession(pageId, pageLabel)
  }

  /** 从应用大纲切换页面；没有消息历史时仅展示空白上下文，不提前创建会话。 */
  const handlePageSelect = (page: DevelopmentPlanningPageOption): void => {
    setPreviewError('')
    setRightPanel(undefined)
    setActiveView('chat')
    setActivePageId(page.pageId)
    handleSelectPage(page.pageId).catch(() => undefined)
  }

  /** 启动当前页面的详细设计；解锁状态仍以后续持久化目录检查为准。 */
  const handleStartPageDesign = async (
    pageId: string,
    pageLabel: string,
    hasDetailPlan: boolean
  ): Promise<void> => {
    setActivePageId(pageId)
    const started = await handleStartDetailConfirmation(pageId, pageLabel, hasDetailPlan)
    if (started) onPlanningArtifactsRefresh()
  }

  const handleOpenChatSession = async (sessionId: string): Promise<void> => {
    setActiveView('chat')
    const sessionPageId = sessions.find((session) => session.id === sessionId)?.pageId
    if (sessionPageId) setActivePageId(sessionPageId)
    await handleOpenSession(sessionId)
  }

  /** 用户关闭技能后立即清理当前会话草稿中的同名标签。 */
  const handleSkillDisabled = (skillName: string): void => {
    const nextSkills = selectedSkills.filter((skill) => skill.name !== skillName)
    if (nextSkills.length !== selectedSkills.length) {
      setSelectedSkillsByKey(draftKey, nextSkills)
    }
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
          onCreatePageSession={handleCreatePageSession}
          onDeleteSession={handleDeleteSession}
          onOpenSession={handleOpenChatSession}
          onPageSelect={handlePageSelect}
          onReturnWelcome={onReturnWelcome}
          onShowFiles={handleShowFiles}
          onShowSettings={handleShowSettings}
          onShowSkills={handleShowSkills}
          pages={developmentPlanningPages}
          apiContracts={developmentPlanningApiContracts}
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
          <SkillsPage
            onSkillDisabled={handleSkillDisabled}
            onThemeChange={onThemeChange}
            theme={theme}
          />
        ) : activeView === 'files' ? (
          <AgentFilesPage />
        ) : activeView === 'settings' ? (
          <SettingsPage application={application} onSaved={onApplicationUpdate} />
        ) : initialPageSelectionRequired ? (
          <div className={cx('ai-chat-main')}>
            <DetailConfirmationPageSelector
              disabled={loading || workspaceBusy}
              generating={loading}
              loading={!developmentPlanningReady}
              onStart={handleStartPageDesign}
              pages={developmentPlanningPages}
              workflowEvents={activeWorkflow?.events}
            />
          </div>
        ) : (
          <div className={cx('ai-chat-main')}>
            <ChatHeader
              actions={
                showPreviewActions ? (
                    <PreviewActions
                      embeddedPreviewOpen={embeddedPreviewOpen}
                      onOpenFullscreenPreview={handleOpenFullscreenPreview}
                      onToggleEmbeddedPreview={() =>
                        setRightPanel(embeddedPreviewOpen ? undefined : { type: 'preview' })
                      }
                    />
                  ) : null
              }
              onThemeChange={onThemeChange}
              pageTitle={activePageTitle}
              theme={theme}
            />

            <PageContextHeader
              description={
                activePageOption?.purpose ||
                activePage?.purpose ||
                application.senario ||
                '当前应用页面'
              }
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
                generating={loading}
                loading={false}
                mode="locked"
                onStart={handleStartPageDesign}
                pages={developmentPlanningPages}
                selectedPage={activePageOption}
                workflowEvents={activeWorkflow?.events}
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
          <BrowserPreviewPanel
            application={application}
            requestKey={rightPanel.requestKey}
            requestedUrl={rightPanel.url}
          />
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
