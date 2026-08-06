import {
  ApiOutlined,
  AuditOutlined,
  CaretDownOutlined,
  DownOutlined,
  FileTextOutlined,
  FilterOutlined,
  FolderOpenOutlined,
  FolderOutlined,
  LeftOutlined,
  LockOutlined,
  RightOutlined,
  SearchOutlined,
  SettingOutlined,
  ThunderboltOutlined
} from '@ant-design/icons'
import { Input, Switch, Typography } from 'antd'
import type { CSSProperties, ReactElement } from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useWorkbenchPhase } from '../../../../context'
import freeChatIcon from '../../../../assets/icons/free-chat.svg'
import recommendedTasksIcon from '../../../../assets/icons/recommended-tasks.svg'
import type { ChatSessionSummary } from '../../../../service/chatSessions'
import type {
  ApplicationMenuItem,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningPageTreeNode,
  DevelopmentPlanningPageOption
} from '../../../../typings'
import { cx } from '../../../../utils'
import { apiEndpointDisplayPath } from '../../utils'
import type { SessionRunStatus } from '../../hooks/sessionRuntime'
import { useCompactWorkbench } from '../../hooks/useCompactWorkbench'
import PageSessionHistory from './PageSessionHistory'
import './SessionSidebar.less'

const { Text } = Typography

// 审查阶段展示的审查清单维度（占位；后续接入真实代码审查 / 健康度检测节点）。
const REVIEW_CHECKLIST = [
  { id: 'code-style', label: '代码规范', status: '待审查', desc: '命名、结构与编码规范一致性' },
  { id: 'health', label: '健康度检测', status: '待审查', desc: '依赖、重复率、复杂度与安全隐患' },
  { id: 'plan-consistency', label: '规划一致性', status: '待审查', desc: '页面 / 接口实现与设计 spec 对齐' },
  { id: 'delivery', label: '交付验收', status: '待验收', desc: '预览效果与需求验收标准核对' }
]

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
  deletingSessionId?: string
  /** 设计阶段强制折叠：大纲无意义，收成 68px 图标栏，禁止展开。 */
  forceCollapsed?: boolean
  filesActive: boolean
  loadingSessions: boolean
  onCreateEndpointSession: (
    apiContractId: string,
    endpointId: string,
    endpointLabel: string
  ) => Promise<void>
  onCreateSession: () => void
  onCreatePageSession: (pageId: string, pageLabel: string) => Promise<void>
  onDeleteSession: (sessionId: string) => Promise<void>
  onOpenSession: (sessionId: string) => Promise<void>
  outlineLocked: boolean
  onApiEndpointSelect: (target: {
    apiContractId: string
    endpointId: string
    endpointKey: string
    label: string
  }) => void
  onPageSelect: (page: DevelopmentPlanningPageOption) => void
  onShowFiles: () => void
  onShowSettings: () => void
  onShowSkills: () => void
  pages: DevelopmentPlanningPageOption[]
  pageTree: DevelopmentPlanningPageTreeNode[]
  selectedApiEndpointKey: string
  selectedPageId: string
  sessionError?: string
  sessionRunStates: Record<string, SessionRunStatus>
  sessions: ChatSessionSummary[]
  settingsActive: boolean
  skillsActive: boolean
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

/** 使用 ProjectPlan 页面清单组织工作台左侧大纲与快捷入口。 */
export default function SessionSidebar({
  activeSessionId,
  apiContracts = [],
  deletingSessionId,
  filesActive,
  forceCollapsed = false,
  loadingSessions,
  onCreateEndpointSession,
  onCreateSession,
  onCreatePageSession,
  onDeleteSession,
  onApiEndpointSelect,
  onOpenSession,
  onPageSelect,
  onShowFiles,
  onShowSettings,
  onShowSkills,
  outlineLocked,
  pages,
  pageTree,
  selectedApiEndpointKey,
  selectedPageId,
  sessionError,
  sessionRunStates,
  sessions,
  settingsActive,
  skillsActive
}: SessionSidebarProps): ReactElement {
  const [outlineQuery, setOutlineQuery] = useState('')
  const [collapsed, setCollapsed] = useState(true)
  const [compactExpanded, setCompactExpanded] = useState(false)
  const [resizing, setResizing] = useState(false)
  const [sidebarWidth, setSidebarWidth] = useState(DEFAULT_SIDEBAR_WIDTH)
  const [pagesExpanded, setPagesExpanded] = useState(true)
  const [apiExpanded, setApiExpanded] = useState(true)
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
  // 阶段感知：需求文档归产品阶段，页面/接口归研发阶段。阶段只在顶部 ribbon 统一切换，
  // 这里只读阶段用于高亮，不在大纲里切阶段。
  const { phase } = useWorkbenchPhase()
  // 大纲按阶段展示：设计阶段折叠成图标栏（forceCollapsed，文档在右侧）；开发阶段只显示 Pages + API。
  const showDevSections = phase === 'development'
  const showReviewSection = phase === 'test'
  const effectiveCollapsed = forceCollapsed
    ? true
    : compactLayout
      ? !compactExpanded
      : collapsed

  // 进入开发阶段时自动展开大纲（配合「自动选中第一个待设计页面」），一次即可，之后可自由折叠。
  const prevPhaseRef = useRef(phase)
  useEffect(() => {
    if (prevPhaseRef.current !== phase && phase === 'development') {
      setCollapsed(false)
    }
    prevPhaseRef.current = phase
  }, [phase])
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
    if (compactLayout || forceCollapsed) return
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
    if (compactLayout || forceCollapsed) return
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
      ) : null}
      <Text className={cx('session-section-title')} strong>
        应用大纲
      </Text>
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
            {showDevSections ? (
              <>
              <section className={cx('outline-section')}>
              <button
                aria-expanded={pagesExpanded}
                className={cx('outline-section-heading')}
                onClick={() => setPagesExpanded((current) => !current)}
                type="button"
              >
                <CaretDownOutlined className={cx(!pagesExpanded && 'collapsed')} />
                <span>Pages</span>
                <span className={cx('outline-phase-tag', 'development')}>开发</span>
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
                <span className={cx('outline-phase-tag', 'development')}>开发</span>
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
              </>
            ) : null}
            {showReviewSection ? (
              <section className={cx('outline-section', 'review-section')}>
                <div className={cx('outline-section-heading')}>
                  <AuditOutlined />
                  <span>审查清单</span>
                  <span className={cx('outline-phase-tag', 'test')}>审查</span>
                </div>
                <div className={cx('review-list')}>
                  {REVIEW_CHECKLIST.map((item) => (
                    <div key={item.id} className={cx('review-row')}>
                      <div className={cx('review-row-main')}>
                        <span className={cx('review-row-label')}>{item.label}</span>
                        <span className={cx('outline-design-status', 'undesign')}>{item.status}</span>
                      </div>
                      <span className={cx('review-row-desc')}>{item.desc}</span>
                    </div>
                  ))}
                </div>
              </section>
            ) : null}
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
        <button onClick={onCreateSession} title="推荐任务" type="button">
          <SidebarAssetIcon source={recommendedTasksIcon} />
          <span>推荐任务</span>
        </button>
        <button onClick={onCreateSession} title="自由对话" type="button">
          <SidebarAssetIcon source={freeChatIcon} />
          <span>自由对话</span>
        </button>
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
        <span className={cx('session-user-avatar')}>S</span>
        <span className={cx('session-user-name')}>Steve Jobs</span>
        <DownOutlined />
      </button>
    </aside>
  )
}
