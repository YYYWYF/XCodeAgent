import {
  FolderOutlined,
  LeftOutlined,
  PlusOutlined,
  RightOutlined,
  SettingOutlined,
  ThunderboltOutlined
} from '@ant-design/icons'
import type { CSSProperties, ReactElement } from 'react'
import { useState } from 'react'
import freeChatIcon from '../../../../assets/icons/free-chat.svg'
import recommendedTasksIcon from '../../../../assets/icons/recommended-tasks.svg'
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
  freeChatActive: boolean
  filesActive: boolean
  forceCollapsed?: boolean
  loadingSessions: boolean
  onCreateFreeChatSession: () => void
  onDeleteSession: (sessionId: string) => Promise<void>
  onOpenFreeChat: () => void
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
  onShowSettings: () => void
  onShowSkills: () => void
  onThemeChange: (theme: 'light' | 'dark') => void
  pages: DevelopmentPlanningPageOption[]
  pageTree: DevelopmentPlanningPageTreeNode[]
  entities: DevelopmentPlanningEntityOption[]
  selectedApiEndpointKey: string
  selectedEntityId: string
  selectedPageId: string
  sessionError?: string
  sessionRunStates: Record<string, SessionRunStatus>
  sessions: ChatSessionSummary[]
  settingsActive: boolean
  skillsActive: boolean
  theme: 'light' | 'dark'
  workspaceRoot: string
}

/** 组织工作台左侧快捷入口，并在非折叠模式下复用应用大纲。 */
export default function SessionSidebar({
  activeSessionId,
  apiContracts = [],
  deletingSessionId,
  entities = [],
  freeChatActive,
  filesActive,
  forceCollapsed = false,
  loadingSessions,
  onCreateFreeChatSession,
  onDeleteSession,
  onOpenFreeChat,
  onApiEndpointSelect,
  onEntitySelect,
  onOpenSession,
  onPageSelect,
  onShowFiles,
  onShowSettings,
  onShowSkills,
  outlineLocked,
  pages,
  pageTree,
  selectedApiEndpointKey,
  selectedEntityId,
  selectedPageId,
  sessionError,
  sessionRunStates,
  sessions,
  settingsActive,
  skillsActive,
  theme
}: SessionSidebarProps): ReactElement {
  const [collapsed, setCollapsed] = useState(false)
  const [resizing, setResizing] = useState(false)
  const [sidebarWidth, setSidebarWidth] = useState(DEFAULT_SIDEBAR_WIDTH)
  const freeChatSessions = sessions
  const compactLayout = useCompactWorkbench()
  // 设计阶段（forceCollapsed）折叠成图标栏：不显示 Page/API 大纲，只留快捷入口图标。
  // 小屏（compactLayout）与大屏共用 collapsed 状态：默认展开常驻左侧，仅手动折叠成图标栏。
  const effectiveCollapsed = forceCollapsed ? true : collapsed

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
        <button aria-disabled="true" disabled title="推荐任务暂不可用" type="button">
          <SidebarAssetIcon source={recommendedTasksIcon} />
          <span>推荐任务</span>
        </button>
        <div className={cx('free-chat-nav-row', freeChatActive && 'active')}>
          <button
            aria-current={freeChatActive ? 'page' : undefined}
            className={cx('free-chat-nav-main')}
            onClick={onOpenFreeChat}
            title="自由对话"
            type="button"
          >
            <SidebarAssetIcon source={freeChatIcon} />
            <span>自由对话</span>
          </button>
          <FreeChatHistory
            activeSessionId={activeSessionId}
            deletingSessionId={deletingSessionId}
            loadingSessions={loadingSessions}
            onDeleteSession={onDeleteSession}
            onOpenSession={onOpenSession}
            sessionError={sessionError}
            sessionRunStates={sessionRunStates}
            sessions={freeChatSessions}
            theme={theme}
          />
          <button
            aria-label="新建自由对话"
            className={cx('free-chat-new-session')}
            onClick={onCreateFreeChatSession}
            title="新建自由对话"
            type="button"
          >
            <PlusOutlined />
          </button>
        </div>
        <button
          className={cx(skillsActive && 'active')}
          onClick={onShowSkills}
          title="技能"
          type="button"
        >
          <ThunderboltOutlined />
          <span>技能</span>
        </button>
        <button
          className={cx(filesActive && 'active')}
          onClick={onShowFiles}
          title="文件"
          type="button"
        >
          <FolderOutlined />
          <span>文件</span>
        </button>
        <button
          className={cx(settingsActive && 'active')}
          onClick={onShowSettings}
          title="设置"
          type="button"
        >
          <SettingOutlined />
          <span>设置</span>
        </button>
      </nav>

      {!effectiveCollapsed ? (
        <ApplicationOutline
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
  )
}
