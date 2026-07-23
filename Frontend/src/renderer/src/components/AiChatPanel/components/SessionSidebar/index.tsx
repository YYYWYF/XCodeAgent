import {
  ApiOutlined,
  CaretDownOutlined,
  DownOutlined,
  FileTextOutlined,
  FilterOutlined,
  FolderOpenOutlined,
  FolderOutlined,
  LeftOutlined,
  LockOutlined,
  MoonOutlined,
  RightOutlined,
  SearchOutlined,
  SettingOutlined,
  SunOutlined,
  ThunderboltOutlined
} from '@ant-design/icons'
import { Input, Switch, Typography } from 'antd'
import type { CSSProperties, ReactElement } from 'react'
import { useEffect, useMemo, useState } from 'react'
import freeChatIcon from '../../../../assets/icons/free-chat.svg'
import recommendedTasksIcon from '../../../../assets/icons/recommended-tasks.svg'
import type { ChatSessionSummary } from '../../../../service/chatSessions'
import type {
  ApplicationConfig,
  ApplicationMenuItem,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningPageOption
} from '../../../../typings'
import { cx } from '../../../../utils'
import type { SessionRunStatus } from '../../hooks/sessionRuntime'
import { useCompactWorkbench } from '../../hooks/useCompactWorkbench'
import PageSessionHistory from './PageSessionHistory'
import './SessionSidebar.less'

const { Text } = Typography

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
  filesActive: boolean
  loadingSessions: boolean
  onCreateSession: () => void
  onCreatePageSession: (pageId: string, pageLabel: string) => Promise<void>
  onDeleteSession: (sessionId: string) => Promise<void>
  onOpenSession: (sessionId: string) => Promise<void>
  outlineLocked: boolean
  onPageSelect: (page: DevelopmentPlanningPageOption) => void
  onReturnWelcome: () => void
  onShowFiles: () => void
  onShowSettings: () => void
  onShowSkills: () => void
  onThemeChange: (theme: 'light' | 'dark') => void
  pages: DevelopmentPlanningPageOption[]
  selectedPageId: string
  sessionError?: string
  sessionRunStates: Record<string, SessionRunStatus>
  sessions: ChatSessionSummary[]
  settingsActive: boolean
  skillsActive: boolean
  theme: 'light' | 'dark'
  workspaceRoot: string
}

type OutlineRowProps = {
  activeSessionId?: string
  deletingSessionId?: string
  designed: boolean
  disabled?: boolean
  item: ApplicationMenuItem
  level: number
  loadingSessions: boolean
  onCreatePageSession: (pageId: string, pageLabel: string) => Promise<void>
  onDeleteSession: (sessionId: string) => Promise<void>
  onOpenSession: (sessionId: string) => Promise<void>
  onSelect: (key: string) => void
  selectedKey: string
  sessionError?: string
  sessionRunStates: Record<string, SessionRunStatus>
  sessions: ChatSessionSummary[]
  visibleKeys: Set<string>
}

/** 渲染单个页面目录节点，并展示其详细设计状态。 */
function OutlineRow({
  activeSessionId,
  deletingSessionId,
  designed,
  disabled = false,
  item,
  level,
  loadingSessions,
  onCreatePageSession,
  onDeleteSession,
  onOpenSession,
  onSelect,
  selectedKey,
  sessionError,
  sessionRunStates,
  sessions,
  visibleKeys
}: OutlineRowProps): ReactElement {
  const [expanded, setExpanded] = useState(true)
  const children = item.children?.filter((child) => visibleKeys.has(child.key)) || []
  const isFolder = item.type === 'menu' || children.length > 0
  const selected = selectedKey === item.key

  return (
    <div className={cx('outline-node')}>
      <button
        aria-current={selected ? 'page' : undefined}
        aria-expanded={isFolder ? expanded : undefined}
        className={cx('outline-row', selected && 'selected')}
        disabled={disabled && !isFolder}
        onClick={() => {
          if (isFolder) setExpanded((current) => !current)
          else if (disabled) return
          else onSelect(item.key)
        }}
        style={{ '--outline-level': level } as React.CSSProperties}
        type="button"
      >
        <span className={cx('outline-caret')}>
          {isFolder ? <CaretDownOutlined className={cx(!expanded && 'collapsed')} /> : null}
        </span>
        <span className={cx('outline-icon')}>
          {isFolder ? <FolderOpenOutlined /> : <FileTextOutlined />}
        </span>
        <span className={cx('outline-label')}>{item.label}</span>
        {!isFolder ? (
          <span className={cx('outline-design-status', designed ? 'designed' : 'undesign')}>
            {designed ? '已设计' : '待设计'}
          </span>
        ) : null}
      </button>
      {!isFolder && !disabled ? (
        <PageSessionHistory
          activeSessionId={activeSessionId}
          deletingSessionId={deletingSessionId}
          loadingSessions={loadingSessions}
          onCreateSession={() => onCreatePageSession(item.pageKey || item.key, item.label)}
          onDeleteSession={onDeleteSession}
          onOpenSession={onOpenSession}
          pageLabel={item.label}
          sessionError={sessionError}
          sessionRunStates={sessionRunStates}
          sessions={sessions}
        />
      ) : null}
      {isFolder && expanded && children.length > 0 ? (
        <div className={cx('outline-children')}>
          {children.map((child) => (
            <OutlineRow
              activeSessionId={activeSessionId}
              deletingSessionId={deletingSessionId}
              disabled={disabled}
              designed={designed}
              item={child}
              key={child.key}
              level={level + 1}
              loadingSessions={loadingSessions}
              onCreatePageSession={onCreatePageSession}
              onDeleteSession={onDeleteSession}
              onOpenSession={onOpenSession}
              onSelect={onSelect}
              selectedKey={selectedKey}
              sessionError={sessionError}
              sessionRunStates={sessionRunStates}
              sessions={sessions.filter((session) => session.pageId === (child.pageKey || child.key))}
              visibleKeys={visibleKeys}
            />
          ))}
        </div>
      ) : null}
    </div>
  )
}

function collectVisibleKeys(items: ApplicationMenuItem[], query: string): Set<string> {
  const visible = new Set<string>()
  const normalizedQuery = query.trim().toLocaleLowerCase()

  const visit = (item: ApplicationMenuItem): boolean => {
    const childMatches = item.children?.some(visit) || false
    const selfMatches = !normalizedQuery || item.label.toLocaleLowerCase().includes(normalizedQuery)
    if (selfMatches || childMatches) visible.add(item.key)
    return selfMatches || childMatches
  }

  items.forEach(visit)
  return visible
}

function collectRelatedKeys(items: ApplicationMenuItem[], selectedKey: string): Set<string> {
  const related = new Set<string>()
  const addDescendants = (item: ApplicationMenuItem): void => {
    related.add(item.key)
    item.children?.forEach(addDescendants)
  }
  const visit = (item: ApplicationMenuItem, ancestors: string[]): boolean => {
    if (item.key === selectedKey) {
      ancestors.forEach((key) => related.add(key))
      addDescendants(item)
      return true
    }
    return item.children?.some((child) => visit(child, [...ancestors, item.key])) || false
  }

  if (!selectedKey) items.forEach(addDescendants)
  else items.some((item) => visit(item, []))
  return related
}

function containsMenuKey(items: ApplicationMenuItem[], key: string): boolean {
  return items.some((item) => item.key === key || containsMenuKey(item.children || [], key))
}

/** 使用 ProjectPlan 页面清单组织工作台左侧大纲与快捷入口。 */
export default function SessionSidebar({
  activeSessionId,
  apiContracts = [],
  application,
  deletingSessionId,
  filesActive,
  loadingSessions,
  onCreateSession,
  onCreatePageSession,
  onDeleteSession,
  onOpenSession,
  onPageSelect,
  onReturnWelcome,
  onShowFiles,
  onShowSettings,
  onShowSkills,
  onThemeChange,
  outlineLocked,
  pages,
  selectedPageId,
  sessionError,
  sessionRunStates,
  sessions,
  settingsActive,
  skillsActive,
  theme,
  workspaceRoot
}: SessionSidebarProps): ReactElement {
  const [outlineQuery, setOutlineQuery] = useState('')
  const [collapsed, setCollapsed] = useState(false)
  const [compactExpanded, setCompactExpanded] = useState(false)
  const [resizing, setResizing] = useState(false)
  const [sidebarWidth, setSidebarWidth] = useState(DEFAULT_SIDEBAR_WIDTH)
  const [pagesExpanded, setPagesExpanded] = useState(true)
  const [apiExpanded, setApiExpanded] = useState(true)
  const [collapsedApiContractIds, setCollapsedApiContractIds] = useState<Set<string>>(() => new Set())
  const [onlyRelated, setOnlyRelated] = useState(false)
  const [selectedApiEndpointId, setSelectedApiEndpointId] = useState('')
  const pageItems = useMemo<ApplicationMenuItem[]>(() => pages.map((page) => ({
    key: page.pageId,
    pageKey: page.pageId,
    path: page.path,
    label: page.label,
    type: 'page',
    purpose: page.purpose,
    keyFeatures: []
  })), [pages])
  const sessionsByPageId = useMemo(() => {
    const groupedSessions = new Map<string, ChatSessionSummary[]>()
    sessions.forEach((session) => {
      if (!session.pageId) return
      const pageSessions = groupedSessions.get(session.pageId) || []
      pageSessions.push(session)
      groupedSessions.set(session.pageId, pageSessions)
    })
    return groupedSessions
  }, [sessions])
  const selectedKey = containsMenuKey(pageItems, selectedPageId) ? selectedPageId : ''
  const visibleKeys = useMemo(() => {
    const matchingKeys = collectVisibleKeys(pageItems, outlineQuery)
    if (!onlyRelated) return matchingKeys
    const relatedKeys = collectRelatedKeys(pageItems, selectedKey)
    if (relatedKeys.size === 0) return matchingKeys
    return new Set([...matchingKeys].filter((key) => relatedKeys.has(key)))
  }, [onlyRelated, outlineQuery, pageItems, selectedKey])
  const compactLayout = useCompactWorkbench()
  const effectiveCollapsed = compactLayout ? !compactExpanded : collapsed
  const visibleApiContracts = useMemo(() => {
    const query = outlineQuery.trim().toLocaleLowerCase()
    if (!query) return apiContracts
    return apiContracts.flatMap((contract) => {
      const contractMatches = contract.label.toLocaleLowerCase().includes(query)
      const endpoints = contractMatches
        ? contract.endpoints
        : contract.endpoints.filter((endpoint) => (
          endpoint.method.toLocaleLowerCase().includes(query)
          || endpoint.path.toLocaleLowerCase().includes(query)
          || endpoint.summary.toLocaleLowerCase().includes(query)
        ))
      return endpoints.length > 0 ? [{ ...contract, endpoints }] : []
    })
  }, [apiContracts, outlineQuery])

  useEffect(() => {
    if (!compactLayout) setCompactExpanded(false)
  }, [compactLayout])

  /** 独立切换一个 API contract 分组，避免多个资源同时收起或展开。 */
  const handleApiContractToggle = (contractId: string): void => {
    setCollapsedApiContractIds((current) => {
      const next = new Set(current)
      if (next.has(contractId)) next.delete(contractId)
      else next.add(contractId)
      return next
    })
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
    setSidebarWidth((current) => Math.min(
      MAX_SIDEBAR_WIDTH,
      Math.max(MIN_SIDEBAR_WIDTH, current + (event.key === 'ArrowLeft' ? -16 : 16))
    ))
  }

  return (
    <aside
      className={cx(
        'session-sidebar',
        effectiveCollapsed && 'collapsed',
        compactLayout && 'compact-layout',
        compactLayout && compactExpanded && 'compact-expanded',
        resizing && 'resizing'
      )}
      aria-label="应用大纲"
      style={{
        '--session-sidebar-width': `${effectiveCollapsed ? COLLAPSED_SIDEBAR_WIDTH : sidebarWidth}px`
      } as React.CSSProperties}
    >
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
          onClick={() => {
            if (compactLayout) setCompactExpanded((current) => !current)
            else setCollapsed((current) => !current)
          }}
          onMouseDown={(event) => event.stopPropagation()}
          title={effectiveCollapsed ? '展开左侧菜单' : '收起左侧菜单'}
          type="button"
        >
          {effectiveCollapsed ? <RightOutlined /> : <LeftOutlined />}
        </button>
      </div>
      <div className={cx('session-sidebar-header')}>
        <button className={cx('session-brand-lockup')} onClick={onReturnWelcome} type="button">
          <span className={cx('session-brand-mark')} aria-hidden="true">
            <i />
            <i />
            <i />
            <i />
          </span>
          <Text className={cx('session-brand')} strong>XCodeAgent</Text>
        </button>
        <button
          aria-label={`切换为${theme === 'dark' ? '浅色' : '深色'}主题`}
          className={cx('session-theme-toggle')}
          onClick={() => onThemeChange(theme === 'dark' ? 'light' : 'dark')}
          title={`切换为${theme === 'dark' ? '浅色' : '深色'}主题`}
          type="button"
        >
          {theme === 'dark' ? <MoonOutlined /> : <SunOutlined />}
        </button>
      </div>

      <button className={cx('session-workspace')} onClick={onReturnWelcome} type="button">
        <span className={cx('session-workspace-icon')}><FileTextOutlined /></span>
        <span className={cx('session-workspace-copy')}>
          <Text className={cx('session-workspace-name')} strong>{application.name}</Text>
          <Text className={cx('session-workspace-description')} title={workspaceRoot}>
            <FolderOutlined />
            <span className={cx('session-workspace-path')}>{workspaceRoot}</span>
          </Text>
        </span>
        <CaretDownOutlined className={cx('session-workspace-arrow')} rotate={-90} />
      </button>

      <Text className={cx('session-section-title')} strong>应用大纲</Text>
      <fieldset
        aria-disabled={outlineLocked}
        aria-label={outlineLocked ? '页面大纲暂不可操作，API 仍可选择' : '应用大纲'}
        className={cx('session-outline-lock-shell')}
      >
      <div className={cx('session-outline-content')}>
          <Input
            allowClear
            aria-label="搜索页面或 API"
            className={cx('session-search')}
            onChange={(event) => setOutlineQuery(event.target.value)}
            placeholder="搜索页面或 API"
            prefix={<SearchOutlined />}
            value={outlineQuery}
          />
          <div className={cx('session-filter-row')}>
            <span><FilterOutlined />只显示与当前选中相关</span>
            <Switch
              aria-label="只显示与当前选中相关"
              checked={onlyRelated}
              onChange={setOnlyRelated}
              size="small"
            />
          </div>

          <div className={cx('session-outline-scroll')}>
        <section className={cx('outline-section')}>
          <button
            aria-expanded={pagesExpanded}
            className={cx('outline-section-heading')}
            onClick={() => setPagesExpanded((current) => !current)}
            type="button"
          >
            <CaretDownOutlined className={cx(!pagesExpanded && 'collapsed')} />
            <span>Pages</span>
          </button>
          {pagesExpanded ? <div className={cx('outline-tree')}>
            {pageItems
              .filter((item) => visibleKeys.has(item.key))
              .map((item) => (
                <OutlineRow
                  activeSessionId={activeSessionId}
                  deletingSessionId={deletingSessionId}
                  disabled={outlineLocked}
                  designed={Boolean(pages.find((page) => page.pageId === item.key)?.designed)}
                  item={item}
                  key={item.key}
                  level={0}
                  loadingSessions={loadingSessions}
                  onCreatePageSession={onCreatePageSession}
                  onDeleteSession={onDeleteSession}
                  onOpenSession={onOpenSession}
                  onSelect={(key) => {
                    const selectedPage = pages.find((page) => page.key === key)
                    if (selectedPage) onPageSelect(selectedPage)
                  }}
                  selectedKey={selectedKey}
                  sessionError={selectedKey === item.key ? sessionError : undefined}
                  sessionRunStates={sessionRunStates}
                  sessions={sessionsByPageId.get(item.pageKey || item.key) || []}
                  visibleKeys={visibleKeys}
                />
              ))}
            {pageItems.length === 0 ? (
              <div className={cx('outline-empty')}>project_plan.json 的 frontend_pages 中暂无页面</div>
            ) : null}
          </div> : null}
        </section>

        <section className={cx('outline-section', 'api-section')}>
          <button
            aria-expanded={apiExpanded}
            className={cx('outline-section-heading')}
            onClick={() => setApiExpanded((current) => !current)}
            type="button"
          >
            <CaretDownOutlined className={cx(!apiExpanded && 'collapsed')} />
            <span>API</span>
          </button>
          {apiExpanded ? <div className={cx('api-group')}>
            {visibleApiContracts.map((contract) => {
              const contractExpanded = !collapsedApiContractIds.has(contract.id)
              return <div key={contract.id}>
                <button
                  aria-expanded={contractExpanded}
                  className={cx('api-group-title')}
                  onClick={() => handleApiContractToggle(contract.id)}
                  type="button"
                >
                  <CaretDownOutlined className={cx(!contractExpanded && 'collapsed')} />
                  <ApiOutlined />
                  <code>{contract.label}</code>
                </button>
                {contractExpanded ? <div className={cx('api-list')}>
                  {contract.endpoints.map((endpoint) => {
                    const endpointKey = `${contract.id}-${endpoint.id}`
                    return (
                      <button
                        aria-current={selectedApiEndpointId === endpointKey ? 'true' : undefined}
                        className={cx('api-row', selectedApiEndpointId === endpointKey && 'selected')}
                        key={endpointKey}
                        onClick={() => setSelectedApiEndpointId(endpointKey)}
                        title={endpoint.summary}
                        type="button"
                      >
                        <span className={cx('api-method', endpoint.method.toLocaleLowerCase())}>{endpoint.method}</span>
                        <code>{endpoint.path}</code>
                      </button>
                    )
                  })}
                </div> : null}
              </div>
            })}
            {visibleApiContracts.length === 0 ? (
              <div className={cx('outline-empty')}>project_plan.json 的 api_contracts 中暂无接口</div>
            ) : null}
          </div> : null}
        </section>

          </div>
      </div>
      {outlineLocked ? (
        <div className={cx('session-outline-lock')}>
          <LockOutlined />
          <Text>页面大纲将在首个页面设计后解锁，API 可先选择查看</Text>
        </div>
      ) : null}
      </fieldset>

      <nav className={cx('session-footer-nav')} aria-label="快捷入口">
        <button onClick={onCreateSession} title="推荐任务" type="button"><SidebarAssetIcon source={recommendedTasksIcon} /><span>推荐任务</span></button>
        <button onClick={onCreateSession} title="自由对话" type="button"><SidebarAssetIcon source={freeChatIcon} /><span>自由对话</span></button>
        <button className={cx(skillsActive && 'active')} onClick={onShowSkills} title="技能" type="button">
          <ThunderboltOutlined /><span>技能</span>
        </button>
        <button className={cx(filesActive && 'active')} onClick={onShowFiles} title="文件" type="button">
          <FolderOutlined /><span>文件</span>
        </button>
        <button className={cx(settingsActive && 'active')} onClick={onShowSettings} title="设置" type="button">
          <SettingOutlined /><span>设置</span>
        </button>
      </nav>

      <button className={cx('session-user')} type="button">
        <span className={cx('session-user-avatar')}>Y</span>
        <span className={cx('session-user-name')}>yifei</span>
        <DownOutlined />
      </button>
    </aside>
  )
}
