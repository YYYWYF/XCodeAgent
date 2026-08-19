import {
  AppstoreOutlined,
  ApiOutlined,
  CaretDownOutlined,
  CodeOutlined,
  DeleteOutlined,
  DownOutlined,
  EllipsisOutlined,
  EditOutlined,
  FileTextOutlined,
  FolderOutlined,
  LeftOutlined,
  MessageOutlined,
  PlusOutlined,
  RightOutlined,
  SettingOutlined,
  ThunderboltOutlined
} from '@ant-design/icons'
import { Dropdown, Menu, Popconfirm, Segmented, Typography } from 'antd'
import type { CSSProperties, ReactElement } from 'react'
import { useMemo, useState } from 'react'
import type { ChatSessionSummary } from '../../../../service/chatSessions'
import type { WorkspaceDocKey } from '../../types'
import type {
  DevelopmentPlanningApiContract,
  DevelopmentPlanningPageOption,
  DevelopmentPlanningPageTreeNode
} from '../../../../typings'
import { cx } from '../../../../utils'
import { apiEndpointDisplayPath } from '../../utils'
import {
  artifactIdsForSession,
  documentArtifactId,
  endpointArtifactId,
  pageArtifactId
} from '../../../../workbenchDomain'
import type { WorkbenchArtifactAccess } from '../../../../workbenchDomain'
import type { WorkbenchArtifactStatus } from '../../../../workbenchDomain'
import './SessionSidebar.less'

const { Text } = Typography
const COLLAPSED_SIDEBAR_WIDTH = 68
const DEFAULT_SIDEBAR_WIDTH = 248

type NavigationView = 'tasks' | 'artifacts'
type ArtifactFilter = 'all' | 'page' | 'endpoint' | 'model'
type ArtifactStatus = 'not-started' | 'in-progress' | 'completed'
type WorkbenchDocumentKey = WorkspaceDocKey | 'code-review'
type WorkbenchPhaseIndex = 1 | 2 | 3

type DesignArtifactItem = {
  available: boolean
  key: WorkbenchDocumentKey
  label: string
  path: string
  status: ArtifactStatus
}

type ArtifactMenuProps = {
  canCreate: boolean
  label: string
  lockMessage?: string
  onCreateTask: () => void
  onOpenSession: (sessionId: string) => Promise<void>
  sessions: ChatSessionSummary[]
}

type ArtifactConversationActionProps = ArtifactMenuProps & {
  disabled: boolean
  theme: 'light' | 'dark'
}

type SessionSidebarProps = {
  activeSessionId?: string
  apiContracts: DevelopmentPlanningApiContract[]
  applicationName: string
  artifactAccessById: Record<string, WorkbenchArtifactAccess>
  artifactStatusById: Record<string, WorkbenchArtifactStatus>
  designArtifacts: DesignArtifactItem[]
  deletingSessionId?: string
  filesActive: boolean
  fixedOpen?: boolean
  readOnly?: boolean
  onApiEndpointSelect: (target: {
    apiContractId: string
    endpointId: string
    endpointKey: string
    label: string
  }) => void
  onCreateEndpointTask: (target: {
    apiContractId: string
    endpointId: string
    endpointLabel: string
  }) => void
  onCreatePageTask: (page: DevelopmentPlanningPageOption) => void
  onCreateSession: () => void
  onCreateDocumentTask: (key: WorkbenchDocumentKey) => void
  onDeleteSession: (sessionId: string) => Promise<void>
  onDesignArtifactSelect: (key: WorkbenchDocumentKey) => void
  onOpenSession: (sessionId: string) => Promise<void>
  onRenameSession: (sessionId: string, title: string) => Promise<void>
  onPageSelect: (page: DevelopmentPlanningPageOption) => void
  onShowFiles: () => void
  onShowSettings: () => void
  onShowSkills: () => void
  pages: DevelopmentPlanningPageOption[]
  pageTree: DevelopmentPlanningPageTreeNode[]
  selectedApiEndpointKey: string
  selectedDesignArtifactKey?: WorkbenchDocumentKey
  selectedPageId: string
  sessions: ChatSessionSummary[]
  settingsActive: boolean
  showDevelopmentTasks: boolean
  skillsActive: boolean
  theme: 'light' | 'dark'
}

/** 判断会话是否关联指定页面，兼容当前单页面会话契约。 */
function sessionMatchesPage(session: ChatSessionSummary, pageId: string): boolean {
  return artifactIdsForSession(session).includes(pageArtifactId(pageId))
}

/** 判断会话是否显式关联指定接口，静态计划依赖不再提前取得产物编辑权。 */
function sessionMatchesEndpoint(
  session: ChatSessionSummary,
  apiContractId: string,
  endpointId: string
): boolean {
  return artifactIdsForSession(session).includes(endpointArtifactId(apiContractId, endpointId))
}

/** 文档产物复用对应阶段的应用级默认对话。 */
function sessionsForDocument(
  sessions: ChatSessionSummary[],
  key: WorkbenchDocumentKey
): ChatSessionSummary[] {
  return sessions.filter((session) =>
    artifactIdsForSession(session).includes(documentArtifactId(key))
  )
}

/** 根据正式产物归属判断对话所属阶段；无产物的自由对话回落到当前工作台阶段。 */
function phaseIndexForSession(
  session: ChatSessionSummary,
  fallback: WorkbenchPhaseIndex
): WorkbenchPhaseIndex {
  const artifactIds = artifactIdsForSession(session)
  if (
    artifactIds.includes(documentArtifactId('code-review')) ||
    (session.title || '').includes('代码审查')
  ) {
    return 3
  }
  if (
    artifactIds.some(
      (artifactId) =>
        artifactId.startsWith('page:') ||
        artifactId.startsWith('endpoint:') ||
        artifactId.startsWith('model:')
    ) ||
    session.pageId ||
    session.endpointId
  ) {
    return 2
  }
  if (
    artifactIds.includes(documentArtifactId('requirement-spec')) ||
    artifactIds.includes(documentArtifactId('project-plan')) ||
    session.title === '应用设计'
  ) {
    return 1
  }
  return fallback
}

/** 渲染目录共用的阶段编号，建立顶部阶段条与左侧对象的视觉对应。 */
function PhaseIndexBadge({ index }: { index: WorkbenchPhaseIndex }): ReactElement {
  return (
    <span aria-label={`第${index}阶段`} className={cx('sidebar-phase-index', `stage-${index}`)}>
      {index}
    </span>
  )
}

/** 渲染已有对话产物的更多菜单，相关对话直接平铺并保留新建入口。 */
function ArtifactMenu({
  canCreate,
  lockMessage,
  onCreateTask,
  onOpenSession,
  sessions
}: ArtifactMenuProps): ReactElement {
  const orderedSessions = [...sessions].sort(
    (left, right) => left.createdAt - right.createdAt || left.id.localeCompare(right.id)
  )
  return (
    <Menu
      className={cx('artifact-conversation-menu')}
      onClick={({ key, domEvent }) => {
        domEvent.stopPropagation()
        if (key === 'new') onCreateTask()
        else void onOpenSession(String(key).replace(/^session:/, ''))
      }}
    >
      <Menu.ItemGroup title="相关对话">
        {orderedSessions.map((session, index) => (
          <Menu.Item key={`session:${session.id}`} icon={<MessageOutlined />}>
            {session.title}
            {index === 0 ? ' · 默认' : ''}
          </Menu.Item>
        ))}
      </Menu.ItemGroup>
      <Menu.Divider />
      <Menu.Item disabled={!canCreate} key="new" icon={<PlusOutlined />}>
        {canCreate ? '基于当前产物新建对话' : lockMessage || '当前版本只读'}
      </Menu.Item>
    </Menu>
  )
}

/** 只有已有相关对话的产物才显示更多菜单；未开始产物直接点击整行授权创建。 */
function ArtifactConversationAction({
  canCreate,
  disabled,
  label,
  lockMessage,
  onCreateTask,
  onOpenSession,
  sessions,
  theme
}: ArtifactConversationActionProps): ReactElement | null {
  if (sessions.length === 0) return null

  return (
    <Dropdown
      disabled={disabled}
      overlay={
        <ArtifactMenu
          canCreate={canCreate}
          label={label}
          lockMessage={lockMessage}
          onCreateTask={onCreateTask}
          onOpenSession={onOpenSession}
          sessions={sessions}
        />
      }
      overlayClassName={cx('artifact-conversation-overlay', theme)}
      placement="bottomLeft"
      trigger={['click']}
    >
      <button aria-label={`${label}操作`} className={cx('artifact-more')} type="button">
        <EllipsisOutlined />
      </button>
    </Dropdown>
  )
}

/** 渲染对话视图：最近对话保持单行，只按关联产物类型过滤。 */
/** 渲染最近对话、类型筛选及对话级重命名和删除操作。 */
function TaskNavigation({
  activeSessionId,
  deletingSessionId,
  filter,
  onCreateSession,
  onDeleteSession,
  onFilterChange,
  onOpenSession,
  onRenameSession,
  readOnly,
  sessions,
  fallbackPhaseIndex
}: {
  activeSessionId?: string
  deletingSessionId?: string
  filter: ArtifactFilter
  onCreateSession: () => void
  onDeleteSession: (sessionId: string) => Promise<void>
  onFilterChange: (filter: ArtifactFilter) => void
  onOpenSession: (sessionId: string) => Promise<void>
  onRenameSession: (sessionId: string, title: string) => Promise<void>
  readOnly: boolean
  sessions: ChatSessionSummary[]
  fallbackPhaseIndex: WorkbenchPhaseIndex
}): ReactElement {
  const [editingSessionId, setEditingSessionId] = useState('')
  const [editingTitle, setEditingTitle] = useState('')
  const visibleSessions = sessions.filter((session) => {
    if (filter === 'page') return Boolean(session.pageId)
    if (filter === 'endpoint') return Boolean(session.endpointId)
    if (filter === 'model') return false
    return true
  })

  /** 提交有效的新名称；空名称回退为原名称，避免生成无标题目录项。 */
  const commitRename = async (session: ChatSessionSummary): Promise<void> => {
    const normalizedTitle = editingTitle.trim()
    setEditingSessionId('')
    setEditingTitle('')
    if (!normalizedTitle || normalizedTitle === session.title) return
    await onRenameSession(session.id, normalizedTitle)
  }

  return (
    <section className={cx('task-navigation')}>
      <header>
        <Text strong>最近对话</Text>
        <button aria-label="新建对话" disabled={readOnly} onClick={onCreateSession} type="button">
          <PlusOutlined />
        </button>
      </header>
      <Segmented
        aria-label="产物类型筛选"
        block
        onChange={(value) => onFilterChange(value as ArtifactFilter)}
        options={[
          { label: '全部', value: 'all' },
          { label: '页面', value: 'page' },
          { label: '接口', value: 'endpoint' },
          { label: '模型', value: 'model' }
        ]}
        size="small"
        value={filter}
      />
      <div className={cx('task-conversation-list')}>
        {visibleSessions.map((session) => (
          <div className={cx('task-conversation-shell')} key={session.id}>
            {editingSessionId === session.id ? (
              <div
                className={cx(
                  'task-conversation-row',
                  'editing',
                  activeSessionId === session.id && 'selected'
                )}
              >
                <PhaseIndexBadge index={phaseIndexForSession(session, fallbackPhaseIndex)} />
                <MessageOutlined />
                <input
                  aria-label="对话名称"
                  autoFocus
                  maxLength={40}
                  onBlur={() => void commitRename(session)}
                  onChange={(event) => setEditingTitle(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') event.currentTarget.blur()
                    if (event.key === 'Escape') {
                      setEditingSessionId('')
                      setEditingTitle('')
                    }
                  }}
                  value={editingTitle}
                />
              </div>
            ) : (
              <button
                className={cx(
                  'task-conversation-row',
                  activeSessionId === session.id && 'selected'
                )}
                onClick={() => void onOpenSession(session.id)}
                title={session.title}
                type="button"
              >
                <PhaseIndexBadge index={phaseIndexForSession(session, fallbackPhaseIndex)} />
                <MessageOutlined />
                <span>{session.title || '新对话'}</span>
              </button>
            )}
            <button
              aria-label={`重命名${session.title}`}
              className={cx('task-conversation-rename')}
              disabled={readOnly}
              onClick={() => {
                setEditingSessionId(session.id)
                setEditingTitle(session.title)
              }}
              type="button"
            >
              <EditOutlined />
            </button>
            <Popconfirm
              cancelText="取消"
              okText="删除"
              onConfirm={() => onDeleteSession(session.id)}
              placement="right"
              title="删除这个对话？"
            >
              <button
                aria-label={`删除${session.title}`}
                className={cx('task-conversation-delete')}
                disabled={readOnly || deletingSessionId === session.id}
                type="button"
              >
                <DeleteOutlined />
              </button>
            </Popconfirm>
          </div>
        ))}
        {visibleSessions.length === 0 ? (
          <button
            className={cx('task-conversation-empty')}
            disabled={readOnly}
            onClick={onCreateSession}
            type="button"
          >
            {readOnly ? '该版本的对话记录只读' : '当前筛选下暂无对话，创建一个新对话'}
          </button>
        ) : null}
      </div>
    </section>
  )
}

/** 递归统计菜单节点下页面总数和已完成数。 */
function pageTreeProgress(node: DevelopmentPlanningPageTreeNode): {
  completed: number
  total: number
} {
  if (node.type === 'page') {
    return { completed: node.designed || node.hasDetailPlan ? 1 : 0, total: 1 }
  }
  return (node.children || []).reduce(
    (result, child) => {
      const progress = pageTreeProgress(child)
      return {
        completed: result.completed + progress.completed,
        total: result.total + progress.total
      }
    },
    { completed: 0, total: 0 }
  )
}

/** 递归渲染页面菜单树，菜单节点只组织层级，页面叶子承载产物操作。 */
function PageArtifactNode({
  artifactAccessById,
  artifactStatusById,
  artifactsAvailable,
  depth,
  node,
  onCreatePageTask,
  onOpenSession,
  onPageSelect,
  pagesById,
  readOnly,
  selectedPageId,
  sessions,
  theme
}: {
  artifactAccessById: Record<string, WorkbenchArtifactAccess>
  artifactStatusById: Record<string, WorkbenchArtifactStatus>
  artifactsAvailable: boolean
  depth: number
  node: DevelopmentPlanningPageTreeNode
  onCreatePageTask: (page: DevelopmentPlanningPageOption) => void
  onOpenSession: (sessionId: string) => Promise<void>
  onPageSelect: (page: DevelopmentPlanningPageOption) => void
  pagesById: Map<string, DevelopmentPlanningPageOption>
  readOnly: boolean
  selectedPageId: string
  sessions: ChatSessionSummary[]
  theme: 'light' | 'dark'
}): ReactElement | null {
  const [expanded, setExpanded] = useState(true)
  if (node.type === 'menu') {
    const progress = pageTreeProgress(node)
    return (
      <div className={cx('artifact-tree-node')}>
        <button
          aria-expanded={expanded}
          className={cx('artifact-branch-row')}
          onClick={() => setExpanded((value) => !value)}
          style={{ paddingLeft: 8 + depth * 14 }}
          type="button"
        >
          <CaretDownOutlined className={cx(!expanded && 'collapsed')} />
          <FolderOutlined />
          <span>{node.label}</span>
          <small>
            {progress.completed}/{progress.total}
          </small>
        </button>
        {expanded ? (
          <div className={cx('artifact-tree-children')}>
            {(node.children || []).map((child) => (
              <PageArtifactNode
                artifactAccessById={artifactAccessById}
                artifactStatusById={artifactStatusById}
                artifactsAvailable={artifactsAvailable}
                depth={depth + 1}
                key={child.key}
                node={child}
                onCreatePageTask={onCreatePageTask}
                onOpenSession={onOpenSession}
                onPageSelect={onPageSelect}
                pagesById={pagesById}
                readOnly={readOnly}
                selectedPageId={selectedPageId}
                sessions={sessions}
                theme={theme}
              />
            ))}
          </div>
        ) : null}
      </div>
    )
  }

  const pageId = node.pageId || node.key
  const page = pagesById.get(pageId)
  if (!page) return null
  const access = artifactAccessById[pageArtifactId(pageId)]
  const related = sessions.filter((session) => sessionMatchesPage(session, pageId))
  const status = artifactStatusById[pageArtifactId(pageId)] || 'not-started'
  const requestsConversation =
    status === 'not-started' && related.length === 0 && !readOnly && access?.mode === 'write'
  return (
    <div className={cx('artifact-row-shell')}>
      <button
        className={cx(
          'artifact-row',
          status,
          selectedPageId === pageId && 'selected',
          access?.mode === 'read' && 'read-only'
        )}
        onClick={() => (requestsConversation ? onCreatePageTask(page) : onPageSelect(page))}
        disabled={!artifactsAvailable || access?.mode === 'unavailable'}
        style={{ paddingLeft: 8 + depth * 14 }}
        title={`${page.label} · ${page.path}${access?.message ? ` · ${access.message}` : ''}`}
        type="button"
      >
        <PhaseIndexBadge index={2} />
        <FileTextOutlined />
        <span className={cx('artifact-label')}>{page.label}</span>
        <span aria-label={status} className={cx('artifact-status-dot', status)} />
      </button>
      <ArtifactConversationAction
        canCreate={!readOnly && access?.mode === 'write'}
        disabled={!artifactsAvailable || access?.mode === 'unavailable'}
        label={page.label}
        lockMessage={access?.message}
        onCreateTask={() => onCreatePageTask(page)}
        onOpenSession={onOpenSession}
        sessions={related}
        theme={theme}
      />
    </div>
  )
}

/** 渲染以应用为根的完整产物树，页面和接口分别保留业务分组。 */
function ArtifactNavigation(
  props: Pick<
    SessionSidebarProps,
    | 'apiContracts'
    | 'applicationName'
    | 'artifactAccessById'
    | 'artifactStatusById'
    | 'designArtifacts'
    | 'onApiEndpointSelect'
    | 'onCreateEndpointTask'
    | 'onCreateDocumentTask'
    | 'onCreatePageTask'
    | 'onDesignArtifactSelect'
    | 'onOpenSession'
    | 'onPageSelect'
    | 'pages'
    | 'pageTree'
    | 'selectedApiEndpointKey'
    | 'selectedDesignArtifactKey'
    | 'selectedPageId'
    | 'sessions'
    | 'showDevelopmentTasks'
    | 'readOnly'
    | 'theme'
  >
): ReactElement {
  const [applicationExpanded, setApplicationExpanded] = useState(true)
  const [pagesExpanded, setPagesExpanded] = useState(true)
  const [apisExpanded, setApisExpanded] = useState(true)
  const [modelsExpanded, setModelsExpanded] = useState(false)
  const [expandedContracts, setExpandedContracts] = useState<Set<string>>(
    () => new Set(props.apiContracts.map((contract) => contract.id))
  )
  const pagesById = useMemo(
    () => new Map(props.pages.map((page) => [page.pageId, page])),
    [props.pages]
  )
  const pageNodes =
    props.pageTree.length > 0
      ? props.pageTree
      : props.pages.map((page) => ({ ...page, type: 'page' as const }))
  const completedPages = props.pages.filter((page) => page.designed || page.hasDetailPlan).length
  const endpointTotal = props.apiContracts.reduce(
    (sum, contract) => sum + contract.endpoints.length,
    0
  )
  const completedEndpoints = props.apiContracts.reduce(
    (sum, contract) =>
      sum +
      contract.endpoints.filter((endpoint) => endpoint.designed || endpoint.hasDetailPlan).length,
    0
  )
  const completedDocuments = props.designArtifacts.filter(
    (artifact) => artifact.status === 'completed'
  ).length
  // 页面、接口和模型只有在项目计划确认保存后才进入正式产物树。
  const developmentArtifactsKnown = props.showDevelopmentTasks
  const completedTotal = completedDocuments + completedPages + completedEndpoints
  const artifactTotal =
    props.designArtifacts.length +
    (developmentArtifactsKnown ? props.pages.length + endpointTotal : 0)

  /** 单独切换一个接口分组，不影响其他契约树节点。 */
  const toggleContract = (contractId: string): void => {
    setExpandedContracts((current) => {
      const next = new Set(current)
      if (next.has(contractId)) next.delete(contractId)
      else next.add(contractId)
      return next
    })
  }

  return (
    <section className={cx('artifact-navigation', 'artifact-tree')}>
      <button
        aria-expanded={applicationExpanded}
        className={cx('artifact-root-row')}
        onClick={() => setApplicationExpanded((value) => !value)}
        type="button"
      >
        <CaretDownOutlined className={cx(!applicationExpanded && 'collapsed')} />
        <AppstoreOutlined />
        <strong>{props.applicationName}</strong>
        <small>
          {completedTotal}/{artifactTotal}
        </small>
      </button>
      {applicationExpanded ? (
        <div className={cx('artifact-root-children')}>
          <div className={cx('artifact-section-row', 'static')}>
            <FileTextOutlined />
            <span>文档</span>
            <small>
              {completedDocuments}/{props.designArtifacts.length}
            </small>
          </div>
          <div className={cx('artifact-tree-children', 'section-children')}>
            {props.designArtifacts.map((artifact) => {
              const related = sessionsForDocument(props.sessions, artifact.key)
              const access = props.artifactAccessById[documentArtifactId(artifact.key)]
              return (
                <div className={cx('artifact-row-shell')} key={artifact.key}>
                  <button
                    className={cx(
                      'artifact-row',
                      artifact.status,
                      props.selectedDesignArtifactKey === artifact.key && 'selected',
                      access?.mode === 'read' && 'read-only'
                    )}
                    disabled={!artifact.available || access?.mode === 'unavailable'}
                    onClick={() => props.onDesignArtifactSelect(artifact.key)}
                    style={{ paddingLeft: 22 }}
                    title={`${artifact.label} · ${artifact.path}${access?.message ? ` · ${access.message}` : ''}`}
                    type="button"
                  >
                    <PhaseIndexBadge index={artifact.key === 'code-review' ? 3 : 1} />
                    <FileTextOutlined />
                    <span className={cx('artifact-label')}>{artifact.label}</span>
                    <span
                      aria-label={artifact.status}
                      className={cx('artifact-status-dot', artifact.status)}
                    />
                  </button>
                  <ArtifactConversationAction
                    canCreate={!props.readOnly && access?.mode === 'write'}
                    disabled={!artifact.available || access?.mode === 'unavailable'}
                    label={artifact.label}
                    lockMessage={access?.message}
                    onCreateTask={() => props.onCreateDocumentTask(artifact.key)}
                    onOpenSession={props.onOpenSession}
                    sessions={related}
                    theme={props.theme}
                  />
                </div>
              )
            })}
          </div>

          {developmentArtifactsKnown ? (
            <>
              <button
                aria-expanded={pagesExpanded}
                className={cx('artifact-section-row')}
                onClick={() => setPagesExpanded((value) => !value)}
                type="button"
              >
                <CaretDownOutlined className={cx(!pagesExpanded && 'collapsed')} />
                <span>页面</span>
                <small>
                  {completedPages}/{props.pages.length}
                </small>
              </button>
              {pagesExpanded ? (
                <div className={cx('artifact-tree-children', 'section-children')}>
                  {pageNodes.map((node) => (
                    <PageArtifactNode
                      artifactAccessById={props.artifactAccessById}
                      artifactStatusById={props.artifactStatusById}
                      artifactsAvailable={props.showDevelopmentTasks}
                      depth={1}
                      key={node.key}
                      node={node}
                      onCreatePageTask={props.onCreatePageTask}
                      onOpenSession={props.onOpenSession}
                      onPageSelect={props.onPageSelect}
                      pagesById={pagesById}
                      readOnly={Boolean(props.readOnly)}
                      selectedPageId={props.selectedPageId}
                      sessions={props.sessions}
                      theme={props.theme}
                    />
                  ))}
                </div>
              ) : null}

              <button
                aria-expanded={apisExpanded}
                className={cx('artifact-section-row')}
                onClick={() => setApisExpanded((value) => !value)}
                type="button"
              >
                <CaretDownOutlined className={cx(!apisExpanded && 'collapsed')} />
                <span>接口</span>
                <small>
                  {completedEndpoints}/{endpointTotal}
                </small>
              </button>
              {apisExpanded ? (
                <div className={cx('artifact-tree-children', 'section-children')}>
                  {props.apiContracts.map((contract) => {
                    const expanded = expandedContracts.has(contract.id)
                    const completed = contract.endpoints.filter(
                      (endpoint) => endpoint.designed || endpoint.hasDetailPlan
                    ).length
                    return (
                      <div className={cx('artifact-tree-node')} key={contract.id}>
                        <button
                          aria-expanded={expanded}
                          className={cx('artifact-branch-row')}
                          onClick={() => toggleContract(contract.id)}
                          style={{ paddingLeft: 22 }}
                          type="button"
                        >
                          <CaretDownOutlined className={cx(!expanded && 'collapsed')} />
                          <FolderOutlined />
                          <span>{contract.label}</span>
                          <small>
                            {completed}/{contract.endpoints.length}
                          </small>
                        </button>
                        {expanded ? (
                          <div className={cx('artifact-tree-children')}>
                            {contract.endpoints.map((endpoint, index) => {
                              const endpointId = endpoint.id || String(index + 1)
                              const apiContractId = endpoint.apiContractId || contract.id
                              const endpointKey = `${apiContractId}:${endpointId}`
                              const path = apiEndpointDisplayPath(endpoint.path, contract.label)
                              const label = `${endpoint.method} ${path}`
                              const related = props.sessions.filter((session) =>
                                sessionMatchesEndpoint(session, apiContractId, endpointId)
                              )
                              const status =
                                props.artifactStatusById[
                                  endpointArtifactId(apiContractId, endpointId)
                                ] || 'not-started'
                              const access =
                                props.artifactAccessById[
                                  endpointArtifactId(apiContractId, endpointId)
                                ]
                              const requestsConversation =
                                status === 'not-started' &&
                                related.length === 0 &&
                                !props.readOnly &&
                                access?.mode === 'write'
                              return (
                                <div className={cx('artifact-row-shell')} key={endpointKey}>
                                  <button
                                    className={cx(
                                      'artifact-row',
                                      status,
                                      props.selectedApiEndpointKey === endpointKey && 'selected',
                                      access?.mode === 'read' && 'read-only'
                                    )}
                                    onClick={() =>
                                      requestsConversation
                                        ? props.onCreateEndpointTask({
                                            apiContractId,
                                            endpointId,
                                            endpointLabel: label
                                          })
                                        : props.onApiEndpointSelect({
                                            apiContractId,
                                            endpointId,
                                            endpointKey,
                                            label
                                          })
                                    }
                                    disabled={
                                      !props.showDevelopmentTasks || access?.mode === 'unavailable'
                                    }
                                    style={{ paddingLeft: 36 }}
                                    title={`${label}${access?.message ? ` · ${access.message}` : ''}`}
                                    type="button"
                                  >
                                    <PhaseIndexBadge index={2} />
                                    <ApiOutlined />
                                    <code className={cx('artifact-label')}>{label}</code>
                                    <span
                                      aria-label={status}
                                      className={cx('artifact-status-dot', status)}
                                    />
                                  </button>
                                  <ArtifactConversationAction
                                    canCreate={!props.readOnly && access?.mode === 'write'}
                                    disabled={
                                      !props.showDevelopmentTasks || access?.mode === 'unavailable'
                                    }
                                    label={label}
                                    lockMessage={access?.message}
                                    onCreateTask={() =>
                                      props.onCreateEndpointTask({
                                        apiContractId,
                                        endpointId,
                                        endpointLabel: label
                                      })
                                    }
                                    onOpenSession={props.onOpenSession}
                                    sessions={related}
                                    theme={props.theme}
                                  />
                                </div>
                              )
                            })}
                          </div>
                        ) : null}
                      </div>
                    )
                  })}
                </div>
              ) : null}

              <button
                aria-expanded={modelsExpanded}
                className={cx('artifact-section-row')}
                onClick={() => setModelsExpanded((value) => !value)}
                type="button"
              >
                <CaretDownOutlined className={cx(!modelsExpanded && 'collapsed')} />
                <span>模型</span>
                <small>0/0</small>
              </button>
              {modelsExpanded ? (
                <div className={cx('artifact-empty')}>
                  <CodeOutlined /> 当前版本暂无模型实体
                </div>
              ) : null}
            </>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}

/** 在同一窄侧栏中切换最近对话与应用产物两种工作视角。 */
export default function SessionSidebar(props: SessionSidebarProps): ReactElement {
  const {
    activeSessionId,
    deletingSessionId,
    filesActive,
    fixedOpen = false,
    onCreateSession,
    onDeleteSession,
    onShowFiles,
    onShowSettings,
    onShowSkills,
    readOnly = false,
    sessions,
    settingsActive,
    skillsActive
  } = props
  const [collapsed, setCollapsed] = useState(false)
  const [filter, setFilter] = useState<ArtifactFilter>('all')
  const [view, setView] = useState<NavigationView>('artifacts')

  const orderedSessions = useMemo(
    () => [...sessions].sort((a, b) => b.updatedAt - a.updatedAt),
    [sessions]
  )
  const effectiveCollapsed = fixedOpen ? false : collapsed

  /** 从产物菜单打开对话时同步切入对话视图，避免内容已切换但目录仍停留在产物树。 */
  const handleOpenArtifactSession = async (sessionId: string): Promise<void> => {
    setView('tasks')
    await props.onOpenSession(sessionId)
  }

  /** 从产物菜单请求新建对话时保留产物树，便于用户在原上下文中完成授权确认。 */
  const handleCreateArtifactConversation = (create: () => void): void => {
    create()
  }

  return (
    <aside
      aria-label="工作台导航"
      className={cx('session-sidebar', effectiveCollapsed && 'collapsed')}
      style={
        {
          '--session-sidebar-width': `${effectiveCollapsed ? COLLAPSED_SIDEBAR_WIDTH : DEFAULT_SIDEBAR_WIDTH}px`
        } as CSSProperties
      }
    >
      {!fixedOpen ? (
        <button
          aria-label={effectiveCollapsed ? '展开左侧菜单' : '收起左侧菜单'}
          className={cx('session-collapse-button', 'standalone')}
          onClick={() => setCollapsed((value) => !value)}
          type="button"
        >
          {effectiveCollapsed ? <RightOutlined /> : <LeftOutlined />}
        </button>
      ) : null}

      <Segmented
        aria-label="导航视图"
        block
        className={cx('navigation-view-switch')}
        onChange={(value) => setView(value as NavigationView)}
        options={[
          { label: '产物视图', value: 'artifacts' },
          { label: '对话视图', value: 'tasks' }
        ]}
        value={view}
      />

      <div className={cx('session-outline-scroll')}>
        {view === 'tasks' ? (
          <TaskNavigation
            activeSessionId={activeSessionId}
            deletingSessionId={deletingSessionId}
            filter={filter}
            onCreateSession={onCreateSession}
            onDeleteSession={onDeleteSession}
            onFilterChange={setFilter}
            onOpenSession={props.onOpenSession}
            onRenameSession={props.onRenameSession}
            readOnly={readOnly}
            sessions={orderedSessions}
            fallbackPhaseIndex={
              props.selectedDesignArtifactKey === 'code-review'
                ? 3
                : props.selectedDesignArtifactKey
                  ? 1
                  : 2
            }
          />
        ) : (
          <ArtifactNavigation
            {...props}
            onCreateDocumentTask={(key) =>
              handleCreateArtifactConversation(() => props.onCreateDocumentTask(key))
            }
            onCreateEndpointTask={(target) =>
              handleCreateArtifactConversation(() => props.onCreateEndpointTask(target))
            }
            onCreatePageTask={(page) =>
              handleCreateArtifactConversation(() => props.onCreatePageTask(page))
            }
            onOpenSession={handleOpenArtifactSession}
            sessions={orderedSessions}
          />
        )}
      </div>

      <div className={cx('session-settings-shell')}>
        <button
          className={cx('session-settings-entry', settingsActive && 'active')}
          onClick={onShowSettings}
          type="button"
        >
          <SettingOutlined />
          <span>应用配置</span>
        </button>
      </div>

      <nav aria-label="快捷入口" className={cx('session-footer-nav')}>
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
      </nav>
      <button className={cx('session-user')} type="button">
        <span className={cx('session-user-avatar')}>S</span>
        <span className={cx('session-user-name')}>Steve Jobs</span>
        <DownOutlined />
      </button>
    </aside>
  )
}
