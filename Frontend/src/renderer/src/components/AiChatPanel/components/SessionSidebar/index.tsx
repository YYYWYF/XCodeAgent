import {
  DatabaseOutlined,
  ApiOutlined,
  FolderOutlined,
  HourglassOutlined,
  LeftOutlined,
  RightOutlined,
  SettingOutlined,
  ThunderboltOutlined
} from '@ant-design/icons'
import type { CSSProperties, ReactElement } from 'react'
import { useEffect, useState } from 'react'
import freeChatIcon from '../../../../assets/icons/free-chat.svg'
import type { ChatSessionSummary } from '../../../../service/chatSessions'
import type {
  ApplicationConfig,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningEntityOption,
  DevelopmentPlanningPageTreeNode,
  DevelopmentPlanningPageOption
} from '../../../../typings'
import { cx } from '../../../../utils'
import type { SessionRunStatus } from '../../hooks/sessionRuntime'
import { useCompactWorkbench } from '../../hooks/useCompactWorkbench'
import ApplicationOutline from '../ApplicationOutline'
import FreeChatHistory from './FreeChatHistory'
import TemporaryChatOverlay from '../TemporaryChatOverlay'
import { useTemporaryChats } from './useTemporaryChats'
import './SessionSidebar.less'

const COLLAPSED_SIDEBAR_WIDTH = 68
const DEFAULT_SIDEBAR_WIDTH = 300
const MIN_SIDEBAR_WIDTH = 240
const MAX_SIDEBAR_WIDTH = 420
const COLLAPSE_DRAG_THRESHOLD = 140

type SidebarAssetIconProps = {
  source: string
}

/** 将集中管理的 SVG 资源渲染为可继承菜单状态颜色的图标。 */
function SidebarAssetIcon({ source }: SidebarAssetIconProps): ReactElement {
  return (
    <span
      aria-hidden="true"
      className={cx('session-footer-icon')}
      style={{ '--session-footer-icon-source': `url("${source}")` } as CSSProperties}
    />
  )
}

type SessionSidebarProps = {
  activeSessionId?: string
  apiContracts: DevelopmentPlanningApiContract[]
  application: ApplicationConfig
  deletingSessionId?: string
  advancingSessionId?: string
  filesActive: boolean
  dataSourcesActive: boolean
  dataSourcesEnabled: boolean
  externalApisActive: boolean
  forceCollapsed?: boolean
  loadingSessions: boolean
  onBeforeNavigate?: (action: () => void) => void
  onCreateFreeChatSession: () => void
  onDeleteSession: (sessionId: string) => Promise<void>
  onOpenSession: (sessionId: string) => Promise<void>
  outlineLocked: boolean
  onApiEndpointSelect: (target: {
    apiContractId: string
    endpointId: string
    endpointKey: string
    label: string
  }) => void
  onEntitySelect: (entity: DevelopmentPlanningEntityOption) => void
  onPageSelect: (page: DevelopmentPlanningPageOption) => void
  onReturnWelcome: () => void
  onShowFiles: () => void
  onShowDataSources: () => void
  onShowExternalApis: () => void
  onShowSettings: () => void
  onShowSkills: () => void
  pages: DevelopmentPlanningPageOption[]
  pageTree: DevelopmentPlanningPageTreeNode[]
  entities: DevelopmentPlanningEntityOption[]
  /** 当前迭代的分支名（= 版本号），用于标注产物归属。 */
  currentBranch?: string
  selectedApiEndpointKey: string
  selectedEntityId: string
  selectedPageId: string
  sessionError?: string
  sessionCreationDisabled: boolean
  sessionRunStates: Record<string, SessionRunStatus>
  sessions: ChatSessionSummary[]
  settingsActive: boolean
  skillsActive: boolean
  /**
   * 是否展示「历史对话 / 新建自由对话」这一组。该能力目前只在开发阶段开放：
   * 会话创建与导航还依赖开发阶段的运行态，其他阶段展示需要一并改这些逻辑，
   * 所以这里单独按阶段门控，不影响文件/技能/设置等其他快捷入口。
   */
  historyEnabled: boolean
  theme: 'light' | 'dark'
  workspaceRoot: string
}

/** 组织工作台左侧快捷入口，并在非折叠模式下复用应用大纲。 */
export default function SessionSidebar({
  activeSessionId,
  apiContracts = [],
  deletingSessionId,
  currentBranch,
  entities = [],
  advancingSessionId,
  filesActive,
  dataSourcesActive,
  dataSourcesEnabled,
  externalApisActive,
  forceCollapsed = false,
  loadingSessions,
  onBeforeNavigate,
  onCreateFreeChatSession,
  onDeleteSession,
  onOpenSession,
  onApiEndpointSelect,
  onEntitySelect,
  onPageSelect,
  onShowFiles,
  onShowDataSources,
  onShowExternalApis,
  onShowSettings,
  onShowSkills,
  outlineLocked,
  pages,
  pageTree,
  selectedApiEndpointKey,
  selectedEntityId,
  selectedPageId,
  sessionError,
  sessionCreationDisabled,
  sessionRunStates,
  sessions,
  settingsActive,
  skillsActive,
  historyEnabled,
  theme,
  workspaceRoot
}: SessionSidebarProps): ReactElement {
  const [collapsed, setCollapsed] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [activeTemporaryChatId, setActiveTemporaryChatId] = useState<string | null>(null)
  const [resizing, setResizing] = useState(false)
  const [sidebarWidth, setSidebarWidth] = useState(DEFAULT_SIDEBAR_WIDTH)
  const temporaryChats = useTemporaryChats(workspaceRoot)
  const activeTemporaryChat = temporaryChats.sessions.find(
    (session) => session.id === activeTemporaryChatId
  )
  const compactLayout = useCompactWorkbench()
  // 设计阶段（forceCollapsed）折叠成图标栏：不显示 Page/API 大纲，只留快捷入口图标。
  // 小屏（compactLayout）与大屏共用 collapsed 状态：默认展开常驻左侧，仅手动折叠成图标栏。
  const effectiveCollapsed = forceCollapsed ? true : collapsed

  /** 关闭历史侧栏并执行用户选择的左栏导航。 */
  const handleRailNavigation = (navigate: () => void): void => {
    const action = (): void => {
      setHistoryOpen(false)
      setActiveTemporaryChatId(null)
      navigate()
    }
    if (onBeforeNavigate) onBeforeNavigate(action)
    else action()
  }

  /** 从历史侧栏新建自由对话，并保留原有持久会话能力。 */
  const handleCreateHistorySession = (): void => {
    if (sessionCreationDisabled) return
    if (onBeforeNavigate) onBeforeNavigate(onCreateFreeChatSession)
    else onCreateFreeChatSession()
  }

  /** 通过唯一入口打开或关闭对话管理面板。 */
  const handleHistoryToggle = (): void => {
    const next = !historyOpen
    const action = (): void => {
      if (next) setActiveTemporaryChatId(null)
      setHistoryOpen(next)
    }
    if (next && onBeforeNavigate) onBeforeNavigate(action)
    else action()
  }

  /** 新建内存临时对话并直接打开只读聊天浮层。 */
  const handleCreateTemporaryChat = (): void => {
    if (!workspaceRoot) return
    const sessionId = temporaryChats.createSession()
    setHistoryOpen(false)
    setActiveTemporaryChatId(sessionId)
  }

  /** 从临时对话列表重新打开当前 Renderer 中保留的会话。 */
  const handleOpenTemporaryChat = (sessionId: string): void => {
    if (!temporaryChats.sessions.some((session) => session.id === sessionId)) return
    setHistoryOpen(false)
    setActiveTemporaryChatId(sessionId)
  }

  /** 从临时对话列表关闭会话并立即清除其消息。 */
  const handleDeleteTemporaryChat = (sessionId: string): void => {
    temporaryChats.deleteSession(sessionId)
    if (activeTemporaryChatId === sessionId) setActiveTemporaryChatId(null)
  }

  /** 返回临时对话列表并保留本次 Renderer 生命周期内的聊天内容。 */
  const handleBackFromTemporaryChat = (): void => {
    setActiveTemporaryChatId(null)
    setHistoryOpen(true)
  }

  /** 关闭临时对话并清除上下文，不写入常规会话历史。 */
  const handleCloseTemporaryChat = (): void => {
    if (activeTemporaryChatId) temporaryChats.deleteSession(activeTemporaryChatId)
    setActiveTemporaryChatId(null)
    setHistoryOpen(false)
  }

  /** 当工作区切换清空临时会话时同步关闭旧会话浮层。 */
  useEffect(() => {
    if (activeTemporaryChatId && !activeTemporaryChat) setActiveTemporaryChatId(null)
  }, [activeTemporaryChat, activeTemporaryChatId])

  /** 从历史侧栏切换会话，保留侧栏以明确当前会话并支持连续切换。 */
  const handleOpenHistorySession = async (sessionId: string): Promise<void> => {
    await onOpenSession(sessionId)
  }

  /** 在常规宽度下启动侧栏拖动调整，窄屏覆盖层保持固定宽度。 */
  const handleResizeStart = (event: React.MouseEvent<HTMLDivElement>): void => {
    if (compactLayout) return
    event.preventDefault()
    const sidebarLeft = event.currentTarget.parentElement?.getBoundingClientRect().left || 0
    setResizing(true)
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    const handleMouseMove = (moveEvent: MouseEvent): void => {
      const nextWidth = moveEvent.clientX - sidebarLeft
      if (nextWidth <= COLLAPSE_DRAG_THRESHOLD) {
        setCollapsed(true)
        return
      }

      setCollapsed(false)
      setSidebarWidth(Math.min(MAX_SIDEBAR_WIDTH, Math.max(MIN_SIDEBAR_WIDTH, nextWidth)))
    }
    const handleMouseUp = (): void => {
      setResizing(false)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
    }

    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)
  }

  /** 支持键盘调整常规侧栏宽度，并在到达最小值后收起。 */
  const handleResizeKeyDown = (event: React.KeyboardEvent<HTMLDivElement>): void => {
    if (compactLayout) return
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
    event.preventDefault()
    if (event.key === 'ArrowLeft' && sidebarWidth <= MIN_SIDEBAR_WIDTH) {
      setCollapsed(true)
      return
    }
    setCollapsed(false)
    setSidebarWidth((current) =>
      Math.min(
        MAX_SIDEBAR_WIDTH,
        Math.max(MIN_SIDEBAR_WIDTH, current + (event.key === 'ArrowLeft' ? -16 : 16))
      )
    )
  }

  return (
    <>
      <aside
        className={cx(
          'session-sidebar',
          effectiveCollapsed && 'collapsed',
          compactLayout && 'compact-layout',
          resizing && 'resizing'
        )}
        aria-label="开发产物"
        style={
          {
            '--session-sidebar-width': `${effectiveCollapsed ? COLLAPSED_SIDEBAR_WIDTH : sidebarWidth}px`
          } as React.CSSProperties
        }
      >
        {!forceCollapsed ? (
          <div
            aria-label="调整左侧菜单宽度"
            aria-orientation="vertical"
            aria-valuemax={MAX_SIDEBAR_WIDTH}
            aria-valuemin={COLLAPSED_SIDEBAR_WIDTH}
            aria-valuenow={effectiveCollapsed ? COLLAPSED_SIDEBAR_WIDTH : sidebarWidth}
            className={cx('session-resize-handle')}
            onKeyDown={handleResizeKeyDown}
            onMouseDown={handleResizeStart}
            role="separator"
            tabIndex={0}
          >
            <button
              aria-label={effectiveCollapsed ? '展开左侧菜单' : '收起左侧菜单'}
              className={cx('session-collapse-button')}
              onClick={() => setCollapsed((current) => !current)}
              onMouseDown={(event) => event.stopPropagation()}
              title={effectiveCollapsed ? '展开左侧菜单' : '收起左侧菜单'}
              type="button"
            >
              {effectiveCollapsed ? <RightOutlined /> : <LeftOutlined />}
            </button>
          </div>
        ) : null}
        <nav className={cx('session-footer-nav')} aria-label="快捷入口">
          <div className={cx('session-rail-primary')}>
            <div className={cx('free-chat-nav-row', historyOpen && 'active')}>
              <button
                aria-expanded={historyOpen}
                aria-label={historyOpen ? '关闭对话管理' : '打开对话管理'}
                className={cx('free-chat-nav-main')}
                onClick={handleHistoryToggle}
                title="对话管理"
                type="button"
              >
                <SidebarAssetIcon source={freeChatIcon} />
                <span>对话管理</span>
              </button>
            </div>
            {dataSourcesEnabled ? (
              <button
                className={cx(dataSourcesActive && 'active')}
                onClick={() => handleRailNavigation(onShowDataSources)}
                title="数据源"
                type="button"
              >
                <DatabaseOutlined />
                <span>数据源</span>
              </button>
            ) : null}
            {dataSourcesEnabled ? (
              <button
                className={cx(externalApisActive && 'active')}
                onClick={() => handleRailNavigation(onShowExternalApis)}
                title="外部 API"
                type="button"
              >
                <ApiOutlined />
                <span>外部 API</span>
              </button>
            ) : null}
            <button aria-disabled="true" disabled title="推荐任务暂不可用" type="button">
              <HourglassOutlined />
              <span>推荐任务</span>
            </button>
          </div>
          <span className={cx('session-rail-divider')} aria-hidden="true" />
          <div className={cx('session-rail-secondary')}>
            <button
              className={cx(filesActive && 'active')}
              onClick={() => handleRailNavigation(onShowFiles)}
              title="文件"
              type="button"
            >
              <FolderOutlined />
              <span>文件</span>
            </button>
            <button
              className={cx(skillsActive && 'active')}
              onClick={() => handleRailNavigation(onShowSkills)}
              title="技能"
              type="button"
            >
              <ThunderboltOutlined />
              <span>技能</span>
            </button>
            <button
              className={cx(settingsActive && 'active')}
              onClick={() => handleRailNavigation(onShowSettings)}
              title="设置"
              type="button"
            >
              <SettingOutlined />
              <span>设置</span>
            </button>
            <span className={cx('session-user')} aria-label="当前用户 S" title="当前用户">
              <span className={cx('session-user-avatar')}>S</span>
              <span className={cx('session-user-name')}>S</span>
            </span>
          </div>
        </nav>

        {!effectiveCollapsed ? (
          <ApplicationOutline
            currentBranch={currentBranch}
            apiContracts={apiContracts}
            entities={entities}
            onApiEndpointSelect={onApiEndpointSelect}
            onEntitySelect={onEntitySelect}
            onPageSelect={onPageSelect}
            outlineLocked={outlineLocked}
            pages={pages}
            pageTree={pageTree}
            selectedApiEndpointKey={selectedApiEndpointKey}
            selectedEntityId={selectedEntityId}
            selectedPageId={selectedPageId}
          />
        ) : null}
      </aside>
      <FreeChatHistory
        key={workspaceRoot}
        open={historyOpen}
        historyEnabled={historyEnabled}
        advancingSessionId={advancingSessionId}
        activeSessionId={activeSessionId}
        deletingSessionId={deletingSessionId}
        loadingSessions={loadingSessions}
        onClose={() => setHistoryOpen(false)}
        onCreateSession={handleCreateHistorySession}
        onCreateTemporaryChat={handleCreateTemporaryChat}
        onDeleteTemporaryChat={handleDeleteTemporaryChat}
        onDeleteSession={onDeleteSession}
        onOpenSession={handleOpenHistorySession}
        onOpenTemporaryChat={handleOpenTemporaryChat}
        sessionCreationDisabled={sessionCreationDisabled}
        sessionError={sessionError}
        sessionRunStates={sessionRunStates}
        sessions={sessions}
        temporaryChats={temporaryChats.sessions}
        temporaryChatEnabled={Boolean(workspaceRoot)}
        theme={theme}
      />
      {activeTemporaryChat ? (
        <TemporaryChatOverlay
          key={activeTemporaryChat.id}
          onBack={handleBackFromTemporaryChat}
          onClose={handleCloseTemporaryChat}
          onSend={(request) => temporaryChats.sendMessage(activeTemporaryChat.id, request)}
          session={activeTemporaryChat}
        />
      ) : null}
    </>
  )
}
