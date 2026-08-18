import {
  ApiOutlined,
  CaretDownOutlined,
  DatabaseOutlined,
  DownOutlined,
  FileTextOutlined,
  FilterOutlined,
  FolderOpenOutlined,
  FolderOutlined,
  LeftOutlined,
  LockOutlined,
  MoonOutlined,
  PlusOutlined,
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
  DevelopmentPlanningEntityOption,
  DevelopmentPlanningPageTreeNode,
  DevelopmentPlanningPageOption
} from '../../../../typings'
import { cx } from '../../../../utils'
import { apiEndpointDisplayPath } from '../../utils'
import type { SessionRunStatus } from '../../hooks/sessionRuntime'
import { useCompactWorkbench } from '../../hooks/useCompactWorkbench'
import PageSessionHistory from './PageSessionHistory'
import FreeChatHistory from './FreeChatHistory'
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
  freeChatActive: boolean
  filesActive: boolean
  loadingSessions: boolean
  onCreateEndpointSession: (
    apiContractId: string,
    endpointId: string,
    endpointLabel: string
  ) => Promise<void>
  onCreateFreeChatSession: () => void
  onCreatePageSession: (pageId: string, pageLabel: string) => Promise<void>
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

type OutlineRowProps = {
  activeSessionId?: string
  deletingSessionId?: string
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

/** 递归统计当前目录节点下的页面数量，用于目录标签展示。 */
function outlineLeafCount(item: ApplicationMenuItem): number {
  if (item.type !== 'menu') return 1
  return (item.children || []).reduce((total, child) => total + outlineLeafCount(child), 0)
}

/** 渲染单个页面目录节点，并展示其详细设计状态。 */
function OutlineRow({
  activeSessionId,
  deletingSessionId,
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
  const designed = Boolean(item.designed)
  const childPageCount = isFolder ? outlineLeafCount(item) : 0

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
          {isFolder
            ? expanded
              ? <FolderOpenOutlined />
              : <FolderOutlined />
            : <FileTextOutlined />}
        </span>
        <span className={cx('outline-copy')}>
          <span className={cx('outline-label-row')}>
            <span className={cx('outline-label')}>{item.label}</span>
            {isFolder ? (
              <span className={cx('outline-menu-count')}>
                {childPageCount} 个页面
              </span>
            ) : (
              <span className={cx('outline-design-status', designed ? 'designed' : 'undesign')}>
                {designed ? '已设计' : '待设计'}
              </span>
            )}
          </span>
          {item.path ? (
            <span className={cx('outline-meta')}>
              {item.path}
            </span>
          ) : null}
        </span>
      </button>
      {!isFolder && !disabled ? (
        <PageSessionHistory
          activeSessionId={activeSessionId}
          deletingSessionId={deletingSessionId}
          loadingSessions={loadingSessions}
          onCreateSession={() => onCreatePageSession(item.pageKey || item.key, item.label)}
          onDeleteSession={onDeleteSession}
          onOpenSession={onOpenSession}
          deleteTitle="删除这个页面会话？"
          emptyDescription="当前页面暂无历史会话"
          sessionError={sessionError}
          sessionRunStates={sessionRunStates}
          sessions={sessions}
          targetLabel={item.label}
        />
      ) : null}
      {isFolder && expanded && children.length > 0 ? (
        <div className={cx('outline-children')}>
          {children.map((child) => (
            <OutlineRow
              activeSessionId={activeSessionId}
              deletingSessionId={deletingSessionId}
              disabled={disabled}
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
              sessions={sessions.filter(
                (session) => session.pageId === (child.pageKey || child.key)
              )}
              visibleKeys={visibleKeys}
            />
          ))}
        </div>
      ) : null}
    </div>
  )
}

/** 把页面树节点递归转换为侧栏复用的菜单项结构。 */
function pageTreeItems(nodes: DevelopmentPlanningPageTreeNode[]): ApplicationMenuItem[] {
  const items: ApplicationMenuItem[] = []
  nodes.forEach((node) => {
    if (node.type === 'menu') {
      const children = pageTreeItems(node.children || [])
      if (children.length === 0) return
      items.push({
        key: node.key,
        path: node.uniquePath || node.path || node.key,
        label: node.label,
        type: 'menu',
        children
      })
      return
    }
    const pageKey = node.pageId || node.key
    if (!pageKey) return
    items.push({
      key: pageKey,
      pageKey,
      path: node.path,
      label: node.label,
      type: 'page',
      purpose: node.purpose,
      keyFeatures: [],
      designed: Boolean(node.designed),
      detailPlanStatus: node.detailPlanStatus,
      hasDetailPlan: node.hasDetailPlan
    })
  })
  return items
}

function collectVisibleKeys(items: ApplicationMenuItem[], query: string): Set<string> {
  const visible = new Set<string>()
  const normalizedQuery = query.trim().toLocaleLowerCase()

  const visit = (item: ApplicationMenuItem): boolean => {
    let childMatches = false
    item.children?.forEach((child) => {
      if (visit(child)) childMatches = true
    })
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

/** 生成 API endpoint 在左侧大纲中的稳定选中键。 */
function apiEndpointSelectionKey(contractId: string, endpointId: string): string {
  return `${contractId}:${endpointId}`
}

/** 判断会话是否属于没有页面、API 或实体归属的自由对话。 */
function isFreeChatSession(session: ChatSessionSummary): boolean {
  return !session.pageId && !session.apiContractId && !session.endpointId && !session.entityId
}

/** 使用 ProjectPlan 页面清单组织工作台左侧大纲与快捷入口。 */
export default function SessionSidebar({
  activeSessionId,
  apiContracts = [],
  application,
  deletingSessionId,
  entities = [],
  freeChatActive,
  filesActive,
  loadingSessions,
  onCreateEndpointSession,
  onCreateFreeChatSession,
  onCreatePageSession,
  onDeleteSession,
  onOpenFreeChat,
  onApiEndpointSelect,
  onEntitySelect,
  onOpenSession,
  onPageSelect,
  onReturnWelcome,
  onShowFiles,
  onShowSettings,
  onShowSkills,
  onThemeChange,
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
  const [entitiesExpanded, setEntitiesExpanded] = useState(true)
  const [collapsedApiContractIds, setCollapsedApiContractIds] = useState<Set<string>>(
    () => new Set()
  )
  const [onlyRelated, setOnlyRelated] = useState(false)
  const pagesById = useMemo(
    () => new Map(pages.map((page) => [page.pageId, page])),
    [pages]
  )
  const pageItems = useMemo<ApplicationMenuItem[]>(
    () => (
      pageTree.length > 0
        ? pageTreeItems(pageTree)
        : pages.map((page) => ({
            key: page.pageId,
            pageKey: page.pageId,
            path: page.path,
            label: page.label,
            type: 'page',
            purpose: page.purpose,
            keyFeatures: [],
            designed: page.designed,
            detailPlanStatus: page.detailPlanStatus,
            hasDetailPlan: page.hasDetailPlan
          }))
    ),
    [pageTree, pages]
  )
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
  const sessionsByEndpointKey = useMemo(() => {
    const groupedSessions = new Map<string, ChatSessionSummary[]>()
    sessions.forEach((session) => {
      const apiContractId = session.apiContractId
      const endpointId = session.endpointId
      if (!apiContractId || !endpointId) return
      const endpointKey = apiEndpointSelectionKey(apiContractId, endpointId)
      const endpointSessions = groupedSessions.get(endpointKey) || []
      endpointSessions.push(session)
      groupedSessions.set(endpointKey, endpointSessions)
    })
    return groupedSessions
  }, [sessions])
  const sessionsByEntityId = useMemo(() => {
    const groupedSessions = new Map<string, ChatSessionSummary[]>()
    sessions.forEach((session) => {
      if (!session.entityId) return
      const entitySessions = groupedSessions.get(session.entityId) || []
      entitySessions.push(session)
      groupedSessions.set(session.entityId, entitySessions)
    })
    return groupedSessions
  }, [sessions])
  const freeChatSessions = useMemo(() => sessions.filter(isFreeChatSession), [sessions])
  const selectedKey = selectedApiEndpointKey
    ? ''
    : containsMenuKey(pageItems, selectedPageId)
      ? selectedPageId
      : ''
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
        : contract.endpoints.filter(
            (endpoint) =>
              endpoint.method.toLocaleLowerCase().includes(query) ||
              endpoint.path.toLocaleLowerCase().includes(query) ||
              endpoint.summary.toLocaleLowerCase().includes(query)
          )
      return endpoints.length > 0 ? [{ ...contract, endpoints }] : []
    })
  }, [apiContracts, outlineQuery])
  const visibleEntities = useMemo(() => {
    const query = outlineQuery.trim().toLocaleLowerCase()
    if (!query) return entities
    return entities.filter(
      (entity) =>
        entity.id.toLocaleLowerCase().includes(query) ||
        entity.label.toLocaleLowerCase().includes(query) ||
        entity.purpose.toLocaleLowerCase().includes(query)
    )
  }, [entities, outlineQuery])

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
        compactLayout && compactExpanded && 'compact-expanded',
        resizing && 'resizing'
      )}
      aria-label="应用大纲"
      style={
        {
          '--session-sidebar-width': `${effectiveCollapsed ? COLLAPSED_SIDEBAR_WIDTH : sidebarWidth}px`
        } as React.CSSProperties
      }
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
          <Text className={cx('session-brand')} strong>
            XCodeAgent
          </Text>
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
        <span className={cx('session-workspace-icon')}>
          <FileTextOutlined />
        </span>
        <span className={cx('session-workspace-copy')}>
          <Text className={cx('session-workspace-name')} strong>
            {application.name}
          </Text>
          <Text className={cx('session-workspace-description')} title={workspaceRoot}>
            <FolderOutlined />
            <span className={cx('session-workspace-path')}>{workspaceRoot}</span>
          </Text>
        </span>
        <CaretDownOutlined className={cx('session-workspace-arrow')} rotate={-90} />
      </button>

      <Text className={cx('session-section-title')} strong>
        应用大纲
      </Text>
      <fieldset
        aria-disabled={outlineLocked}
        aria-label={outlineLocked ? '页面大纲暂不可操作，API 与实体仍可选择' : '应用大纲'}
        className={cx('session-outline-lock-shell')}
      >
        <div className={cx('session-outline-content')}>
          <Input
            allowClear
            aria-label="搜索页面、API 或实体"
            className={cx('session-search')}
            onChange={(event) => setOutlineQuery(event.target.value)}
            placeholder="搜索页面、API 或实体"
            prefix={<SearchOutlined />}
            value={outlineQuery}
          />
          <div className={cx('session-filter-row')}>
            <span>
              <FilterOutlined />
              只显示与当前选中相关
            </span>
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
              {pagesExpanded ? (
                <div className={cx('outline-tree')}>
                  {pageItems
                    .filter((item) => visibleKeys.has(item.key))
                    .map((item) => (
                      <OutlineRow
                        activeSessionId={activeSessionId}
                        deletingSessionId={deletingSessionId}
                        disabled={outlineLocked}
                        item={item}
                        key={item.key}
                        level={0}
                        loadingSessions={loadingSessions}
                        onCreatePageSession={onCreatePageSession}
                        onDeleteSession={onDeleteSession}
                        onOpenSession={onOpenSession}
                        onSelect={(key) => {
                          const selectedPage = pagesById.get(key)
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
                    <div className={cx('outline-empty')}>
                      project_plan.json 的 frontend_pages 中暂无页面
                    </div>
                  ) : null}
                </div>
              ) : null}
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
              {apiExpanded ? (
                <div className={cx('api-group')}>
                  {visibleApiContracts.map((contract) => {
                    const contractExpanded = !collapsedApiContractIds.has(contract.id)
                    return (
                      <div key={contract.id}>
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
                        {contractExpanded ? (
                          <div className={cx('api-list')}>
                            {contract.endpoints.map((endpoint, endpointIndex) => {
                              const endpointId = endpoint.id || String(endpointIndex + 1)
                              const apiContractId = endpoint.apiContractId || contract.id
                              const endpointKey = apiEndpointSelectionKey(apiContractId, endpointId)
                              const displayPath = apiEndpointDisplayPath(
                                endpoint.path,
                                contract.label
                              )
                              const endpointLabel = `${endpoint.method} ${displayPath}`.trim()
                              const endpointDesigned = Boolean(
                                endpoint.designed || endpoint.hasDetailPlan
                              )
                              const endpointSessions = sessionsByEndpointKey.get(endpointKey) || []
                              return (
                                <div className={cx('api-node')} key={endpointKey}>
                                  <button
                                    aria-current={
                                      selectedApiEndpointKey === endpointKey ? 'true' : undefined
                                    }
                                    className={cx(
                                      'api-row',
                                      selectedApiEndpointKey === endpointKey && 'selected'
                                    )}
                                    onClick={() =>
                                      onApiEndpointSelect({
                                        apiContractId,
                                        endpointId,
                                        endpointKey,
                                        label: endpointLabel
                                      })
                                    }
                                    title={endpoint.summary}
                                    type="button"
                                  >
                                    <span
                                      className={cx(
                                        'api-method',
                                        endpoint.method.toLocaleLowerCase()
                                      )}
                                    >
                                      {endpoint.method}
                                    </span>
                                    <code>{displayPath}</code>
                                    <span
                                      className={cx(
                                        'outline-design-status',
                                        endpointDesigned ? 'designed' : 'undesign'
                                      )}
                                    >
                                      {endpointDesigned ? '已设计' : '待设计'}
                                    </span>
                                  </button>
                                  <PageSessionHistory
                                    activeSessionId={activeSessionId}
                                    deletingSessionId={deletingSessionId}
                                    deleteTitle="删除这个接口会话？"
                                    emptyDescription="当前接口暂无历史会话"
                                    loadingSessions={loadingSessions}
                                    onCreateSession={() =>
                                      onCreateEndpointSession(
                                        apiContractId,
                                        endpointId,
                                        endpointLabel
                                      )
                                    }
                                    onDeleteSession={onDeleteSession}
                                    onOpenSession={onOpenSession}
                                    sessionError={
                                      selectedApiEndpointKey === endpointKey
                                        ? sessionError
                                        : undefined
                                    }
                                    sessionRunStates={sessionRunStates}
                                    sessions={endpointSessions}
                                    targetLabel={endpointLabel}
                                  />
                                </div>
                              )
                            })}
                          </div>
                        ) : null}
                      </div>
                    )
                  })}
                  {visibleApiContracts.length === 0 ? (
                    <div className={cx('outline-empty')}>
                      project_plan.json 的 api_contracts 中暂无接口
                    </div>
                  ) : null}
                </div>
              ) : null}
            </section>

            <section className={cx('outline-section', 'entity-section')}>
              <button
                aria-expanded={entitiesExpanded}
                className={cx('outline-section-heading')}
                onClick={() => setEntitiesExpanded((current) => !current)}
                type="button"
              >
                <CaretDownOutlined className={cx(!entitiesExpanded && 'collapsed')} />
                <span>Entities</span>
              </button>
              {entitiesExpanded ? (
                <div className={cx('entity-group')}>
                  {visibleEntities.map((entity) => {
                    const entityKey = entity.id
                    const entityDesigned = Boolean(entity.designed || entity.hasDetailPlan)
                    return (
                      <div className={cx('entity-node')} key={entityKey}>
                        <button
                          aria-current={selectedEntityId === entityKey ? 'true' : undefined}
                          className={cx(
                            'entity-row',
                            selectedEntityId === entityKey && 'selected'
                          )}
                          onClick={() => onEntitySelect(entity)}
                          title={entity.purpose}
                          type="button"
                        >
                          <span className={cx('entity-icon')}>
                            <DatabaseOutlined />
                          </span>
                          <span className={cx('entity-copy')}>
                            <span className={cx('outline-label-row')}>
                              <span className={cx('outline-label')}>{entity.label}</span>
                              <span
                                className={cx(
                                  'outline-design-status',
                                  entityDesigned ? 'designed' : 'undesign'
                                )}
                              >
                                {entityDesigned ? '已设计' : '待设计'}
                              </span>
                            </span>
                            <span className={cx('entity-meta')}>
                              {entity.id}
                            </span>
                          </span>
                        </button>
                        <PageSessionHistory
                          activeSessionId={activeSessionId}
                          deletingSessionId={deletingSessionId}
                          loadingSessions={loadingSessions}
                          onCreateSession={async () => onEntitySelect(entity)}
                          onDeleteSession={onDeleteSession}
                          onOpenSession={onOpenSession}
                          deleteTitle="删除这个实体会话？"
                          emptyDescription="当前实体暂无历史会话"
                          sessionError={sessionError}
                          sessionRunStates={sessionRunStates}
                          sessions={sessionsByEntityId.get(entity.id) || []}
                          targetLabel={entity.label}
                        />
                      </div>
                    )
                  })}
                  {visibleEntities.length === 0 ? (
                    <div className={cx('outline-empty')}>
                      project_plan.json 的 entities 中暂无实体
                    </div>
                  ) : null}
                </div>
              ) : null}
            </section>
          </div>
        </div>
        {outlineLocked ? (
          <div className={cx('session-outline-lock')}>
            <LockOutlined />
            <Text>完成首次设计后解锁</Text>
          </div>
        ) : null}
      </fieldset>

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

      <button className={cx('session-user')} type="button">
        <span className={cx('session-user-avatar')}>Y</span>
        <span className={cx('session-user-name')}>yifei</span>
        <DownOutlined />
      </button>
    </aside>
  )
}
