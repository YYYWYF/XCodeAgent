import {
  ApiOutlined,
  CaretDownOutlined,
  DownOutlined,
  FileTextOutlined,
  FilterOutlined,
  FolderOpenOutlined,
  FolderOutlined,
  LeftOutlined,
  RightOutlined,
  SearchOutlined,
  SettingOutlined,
  ThunderboltOutlined
} from '@ant-design/icons'
import { Input, Switch, Typography } from 'antd'
import type { CSSProperties, KeyboardEvent, ReactElement } from 'react'
import { useEffect, useMemo, useState } from 'react'
import freeChatIcon from '../../../../assets/icons/free-chat.svg'
import recommendedTasksIcon from '../../../../assets/icons/recommended-tasks.svg'
import type { ChatSessionSummary } from '../../../../service/chatSessions'
import type { ApplicationConfig, ApplicationMenuItem } from '../../../../typings'
import { cx } from '../../../../utils'
import type { SessionRunStatus } from '../../hooks/sessionRuntime'
import './SessionSidebar.less'

const { Text } = Typography

const COLLAPSED_SIDEBAR_WIDTH = 68
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
  application: ApplicationConfig
  deletingSessionId?: string
  filesActive: boolean
  loadingSessions: boolean
  onCreateSession: () => void
  onDeleteSession: (sessionId: string) => Promise<void>
  onOpenSession: (sessionId: string) => Promise<void>
  onOpenSessionKeyDown: (event: KeyboardEvent<HTMLDivElement>, sessionId: string) => void
  onPageSelect: (label: string) => void
  onReturnWelcome: () => void
  onShowFiles: () => void
  onShowSettings: () => void
  onShowSkills: () => void
  sessionError?: string
  sessionRunStates: Record<string, SessionRunStatus>
  sessions: ChatSessionSummary[]
  settingsActive: boolean
  skillsActive: boolean
  workspaceRoot: string
}

type OutlineRowProps = {
  item: ApplicationMenuItem
  level: number
  onSelect: (key: string, label: string) => void
  selectedKey: string
  visibleKeys: Set<string>
}

const API_ITEMS = [
  { method: 'POST', path: '/api/leave/applications' },
  { method: 'GET', path: '/api/leave/applications' },
  { method: 'GET', path: '/api/leave/applications/{id}' },
  { method: 'PUT', path: '/api/leave/applications/{id}' },
  { method: 'DELETE', path: '/api/leave/applications/{id}' }
]

function OutlineRow({ item, level, onSelect, selectedKey, visibleKeys }: OutlineRowProps) {
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
        onClick={() => {
          if (isFolder) setExpanded((current) => !current)
          else onSelect(item.key, item.label)
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
      </button>
      {isFolder && expanded && children.length > 0 ? (
        <div className={cx('outline-children')}>
          {children.map((child) => (
            <OutlineRow
              item={child}
              key={child.key}
              level={level + 1}
              onSelect={onSelect}
              selectedKey={selectedKey}
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

export default function SessionSidebar({
  application,
  filesActive,
  onCreateSession,
  onPageSelect,
  onReturnWelcome,
  onShowFiles,
  onShowSettings,
  onShowSkills,
  settingsActive,
  skillsActive
}: SessionSidebarProps): ReactElement {
  const [outlineQuery, setOutlineQuery] = useState('')
  const [collapsed, setCollapsed] = useState(false)
  const [resizing, setResizing] = useState(false)
  const [sidebarWidth, setSidebarWidth] = useState(334)
  const [pagesExpanded, setPagesExpanded] = useState(true)
  const [apiExpanded, setApiExpanded] = useState(true)
  const [apiGroupExpanded, setApiGroupExpanded] = useState(true)
  const [onlyRelated, setOnlyRelated] = useState(false)
  const initialSelectedKey = application.menus.homeMenuKey || application.menus.items[0]?.key || ''
  const [selectedKey, setSelectedKey] = useState(initialSelectedKey)
  useEffect(() => {
    setSelectedKey((current) => (
      current && containsMenuKey(application.menus.items, current)
        ? current
        : initialSelectedKey
    ))
  }, [application.menus.items, initialSelectedKey])
  const visibleKeys = useMemo(() => {
    const matchingKeys = collectVisibleKeys(application.menus.items, outlineQuery)
    if (!onlyRelated) return matchingKeys
    const relatedKeys = collectRelatedKeys(application.menus.items, selectedKey)
    if (relatedKeys.size === 0) return matchingKeys
    return new Set([...matchingKeys].filter((key) => relatedKeys.has(key)))
  }, [application.menus.items, onlyRelated, outlineQuery, selectedKey])
  const appDescription = application.senario || '智能应用设计与开发工作区'

  const handleResizeStart = (event: React.MouseEvent<HTMLDivElement>): void => {
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

  const handleResizeKeyDown = (event: React.KeyboardEvent<HTMLDivElement>): void => {
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
      className={cx('session-sidebar', collapsed && 'collapsed', resizing && 'resizing')}
      aria-label="应用大纲"
      style={{
        '--session-sidebar-width': `${collapsed ? COLLAPSED_SIDEBAR_WIDTH : sidebarWidth}px`
      } as React.CSSProperties}
    >
      <div
        aria-label="调整左侧菜单宽度"
        aria-orientation="vertical"
        aria-valuemax={MAX_SIDEBAR_WIDTH}
        aria-valuemin={COLLAPSED_SIDEBAR_WIDTH}
        aria-valuenow={collapsed ? COLLAPSED_SIDEBAR_WIDTH : sidebarWidth}
        className={cx('session-resize-handle')}
        onKeyDown={handleResizeKeyDown}
        onMouseDown={handleResizeStart}
        role="separator"
        tabIndex={0}
      >
        <button
          aria-label={collapsed ? '展开左侧菜单' : '收起左侧菜单'}
          className={cx('session-collapse-button')}
          onClick={() => setCollapsed((current) => !current)}
          onMouseDown={(event) => event.stopPropagation()}
          title={collapsed ? '展开左侧菜单' : '收起左侧菜单'}
          type="button"
        >
          {collapsed ? <RightOutlined /> : <LeftOutlined />}
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
      </div>

      <button className={cx('session-workspace')} onClick={onReturnWelcome} type="button">
        <span className={cx('session-workspace-icon')}><FileTextOutlined /></span>
        <span className={cx('session-workspace-copy')}>
          <Text className={cx('session-workspace-name')} strong>{application.name}</Text>
          <Text className={cx('session-workspace-description')}>{appDescription}</Text>
        </span>
        <CaretDownOutlined className={cx('session-workspace-arrow')} rotate={-90} />
      </button>

      <Text className={cx('session-section-title')} strong>应用大纲</Text>
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
            {application.menus.items
              .filter((item) => visibleKeys.has(item.key))
              .map((item) => (
                <OutlineRow
                  item={item}
                  key={item.key}
                  level={0}
                  onSelect={(key, label) => {
                    setSelectedKey(key)
                    onPageSelect(label)
                  }}
                  selectedKey={selectedKey}
                  visibleKeys={visibleKeys}
                />
              ))}
            {application.menus.items.length === 0 ? (
              <div className={cx('outline-empty')}>暂无页面，请先在对话中创建页面</div>
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
            <button
              aria-expanded={apiGroupExpanded}
              className={cx('api-group-title')}
              onClick={() => setApiGroupExpanded((current) => !current)}
              type="button"
            >
              <CaretDownOutlined className={cx(!apiGroupExpanded && 'collapsed')} />
              <ApiOutlined />
              <span>请假相关接口</span>
            </button>
            {apiGroupExpanded ? <div className={cx('api-list')}>
              {API_ITEMS.map((item, index) => (
                <button className={cx('api-row')} key={`${item.method}-${index}`} type="button">
                  <span className={cx('api-method', item.method.toLocaleLowerCase())}>{item.method}</span>
                  <code>{item.path}</code>
                </button>
              ))}
            </div> : null}
          </div> : null}
        </section>

          </div>
      </div>

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
