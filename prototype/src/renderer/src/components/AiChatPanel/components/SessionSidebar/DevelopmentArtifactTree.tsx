import {
  ApiOutlined,
  AppstoreOutlined,
  CaretDownOutlined,
  CaretRightOutlined,
  FileTextOutlined,
  FolderOutlined
} from '@ant-design/icons'
import type { ReactElement } from 'react'
import { useMemo, useState } from 'react'
import type { WorkspaceDocKey } from '../../types'
import { appApiArtifactId, type AppApi } from '../../../AppApis/model'
import { groupAppApis } from '../../../AppApis/grouping'
import type {
  DevelopmentPlanningApiContract,
  DevelopmentPlanningPageOption,
  DevelopmentPlanningPageTreeNode
} from '../../../../typings'
import { cx } from '../../../../utils'
import { apiEndpointDisplayPath } from '../../utils'
import {
  documentArtifactId,
  endpointArtifactId,
  pageArtifactId
} from '../../../../workbenchDomain'
import type { WorkbenchArtifactAccess } from '../../../../workbenchDomain'
import type { WorkbenchArtifactStatus } from '../../../../workbenchDomain'
import './SessionSidebar.less'

type ArtifactStatus = 'not-started' | 'in-progress' | 'completed'
type WorkbenchDocumentKey = WorkspaceDocKey | 'code-review'
type DesignArtifactItem = {
  available: boolean
  key: WorkbenchDocumentKey
  label: string
  path: string
  status: ArtifactStatus
}

/** 递归统计菜单节点下页面总数和已完成数，测试发现缺陷时同步反映产物回到进行中。 */
function pageTreeProgress(
  node: DevelopmentPlanningPageTreeNode,
  artifactStatusById?: Record<string, WorkbenchArtifactStatus>
): {
  completed: number
  total: number
} {
  if (node.type === 'page') {
    const status = artifactStatusById?.[pageArtifactId(node.pageId || node.key)]
    return {
      completed: status
        ? status === 'completed'
          ? 1
          : 0
        : node.designed || node.hasDetailPlan
          ? 1
          : 0,
      total: 1
    }
  }
  return (node.children || []).reduce(
    (result, child) => {
      const progress = pageTreeProgress(child, artifactStatusById)
      return {
        completed: result.completed + progress.completed,
        total: result.total + progress.total
      }
    },
    { completed: 0, total: 0 }
  )
}

/** 递归渲染应用页面菜单树，菜单节点只组织层级，页面叶子承载产物操作。 */
function PageArtifactNode({
  artifactAccessById,
  artifactStatusById,
  artifactsAvailable,
  depth,
  node,
  onCreatePageTask,
  onPageSelect,
  pagesById,
  readOnly,
  selectedPageId
}: {
  artifactAccessById: Record<string, WorkbenchArtifactAccess>
  artifactStatusById: Record<string, WorkbenchArtifactStatus>
  artifactsAvailable: boolean
  depth: number
  node: DevelopmentPlanningPageTreeNode
  onCreatePageTask: (page: DevelopmentPlanningPageOption) => void
  onPageSelect: (page: DevelopmentPlanningPageOption) => void
  pagesById: Map<string, DevelopmentPlanningPageOption>
  readOnly: boolean
  selectedPageId: string
}): ReactElement | null {
  const [expanded, setExpanded] = useState(true)
  if (node.type === 'menu') {
    const progress = pageTreeProgress(node, artifactStatusById)
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
                onPageSelect={onPageSelect}
                pagesById={pagesById}
                readOnly={readOnly}
                selectedPageId={selectedPageId}
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
  const status = artifactStatusById[pageArtifactId(pageId)] || 'not-started'
  const requestsWorkflow = status === 'not-started' && !readOnly && access?.mode === 'write'
  return (
    <div className={cx('artifact-row-shell')}>
      <button
        className={cx(
          'artifact-row',
          status,
          selectedPageId === pageId &&
            artifactsAvailable &&
            access?.mode !== 'unavailable' &&
            'selected',
          access?.mode === 'read' && 'read-only'
        )}
        onClick={() => (requestsWorkflow ? onCreatePageTask(page) : onPageSelect(page))}
        disabled={
          !artifactsAvailable || access?.mode === 'unavailable' || access?.reason === 'phase-locked'
        }
        style={{ paddingLeft: 8 + depth * 14 }}
        title={`${page.label} · ${page.path}${access?.message ? ` · ${access.message}` : ''}`}
        type="button"
      >
        <FileTextOutlined />
        <span className={cx('artifact-label')}>{page.label}</span>
        <span aria-label={status} className={cx('artifact-status-dot', status)} />
      </button>
    </div>
  )
}

/** 产物树自身的属性契约（原从 SessionSidebarProps Pick，侧栏删除后显式声明）。 */
type ArtifactNavigationProps = {
  apiContracts: DevelopmentPlanningApiContract[]
  applicationName: string
  appApis: AppApi[]
  artifactAccessById: Record<string, WorkbenchArtifactAccess>
  artifactStatusById: Record<string, WorkbenchArtifactStatus>
  designArtifacts: DesignArtifactItem[]
  /** 开发产物工作区不展示需求分析/项目计划阶段文档，只复用原有开发目录树。 */
  hideDesignArtifacts?: boolean
  /** 开发产物目录不展示应用根节点，应用页面/应用API直接作为一级分组。 */
  hideApplicationRoot?: boolean
  /** 选择应用API后在右侧打开契约与数据绑定工作台。 */
  onAppApiSelect?: (objectId: string) => void
  onApiEndpointSelect: (target: {
    apiContractId: string
    endpointId: string
    endpointKey: string
    label: string
  }) => void
  onCreateDocumentTask: (key: WorkbenchDocumentKey) => void
  onCreateEndpointTask: (target: {
    apiContractId: string
    endpointId: string
    endpointLabel: string
  }) => void
  onCreatePageTask: (page: DevelopmentPlanningPageOption) => void
  onDesignArtifactSelect: (key: WorkbenchDocumentKey) => void
  onPageSelect: (page: DevelopmentPlanningPageOption) => void
  pages: DevelopmentPlanningPageOption[]
  pageTree: DevelopmentPlanningPageTreeNode[]
  readOnly?: boolean
  selectedApiEndpointKey: string
  selectedDesignArtifactKey?: WorkbenchDocumentKey
  selectedPageId: string
  selectedAppApiId?: string
  showDevelopmentTasks: boolean
}

/** 渲染以应用为根的完整产物树，应用页面和应用API分别保留业务分组。 */
function ArtifactNavigation(props: ArtifactNavigationProps): ReactElement {
  const [applicationExpanded, setApplicationExpanded] = useState(true)
  const [pagesExpanded, setPagesExpanded] = useState(true)
  const [apisExpanded, setApisExpanded] = useState(true)
  const [appApisExpanded, setAppApisExpanded] = useState(true)
  // 应用API分组默认全部展开（与页面菜单一致），集合里只记录被手动折叠的分组。
  const [collapsedApiGroups, setCollapsedApiGroups] = useState<Set<string>>(() => new Set())
  const [expandedContracts, setExpandedContracts] = useState<Set<string>>(
    () => new Set(props.apiContracts.map((contract) => contract.id))
  )
  const appApiGroups = useMemo(
    () => groupAppApis(props.appApis, props.pageTree),
    [props.appApis, props.pageTree]
  )
  const pagesById = useMemo(
    () => new Map(props.pages.map((page) => [page.pageId, page])),
    [props.pages]
  )
  const pageNodes =
    props.pageTree.length > 0
      ? props.pageTree
      : props.pages.map((page) => ({ ...page, type: 'page' as const }))
  const completedPages = props.pages.filter(
    (page) => props.artifactStatusById[pageArtifactId(page.pageId)] === 'completed'
  ).length
  const endpointTotal = props.apiContracts.reduce(
    (sum, contract) => sum + contract.endpoints.length,
    0
  )
  const completedEndpoints = props.apiContracts.reduce(
    (sum, contract) =>
      sum +
      contract.endpoints.filter((endpoint, endpointIndex) => {
        const endpointId = endpoint.id || String(endpointIndex + 1)
        const apiContractId = endpoint.apiContractId || contract.id
        return (
          props.artifactStatusById[endpointArtifactId(apiContractId, endpointId)] === 'completed'
        )
      }).length,
    0
  )
  const completedAppApis = props.appApis.filter(
    (object) => props.artifactStatusById[appApiArtifactId(object.id)] === 'completed'
  ).length
  const completedDocuments = props.designArtifacts.filter(
    (artifact) => artifact.status === 'completed'
  ).length
  // 页面与应用API只有在项目计划确认保存后才进入正式产物树。
  const developmentArtifactsKnown = props.showDevelopmentTasks
  const completedTotal = completedDocuments + completedPages + completedAppApis
  const artifactTotal =
    props.designArtifacts.length +
    (developmentArtifactsKnown ? props.pages.length + props.appApis.length : 0)

  /** 单独折叠/展开一个应用API分组，不影响其他分组。 */
  const toggleAppApiGroup = (groupKey: string): void => {
    setCollapsedApiGroups((current) => {
      const next = new Set(current)
      if (next.has(groupKey)) next.delete(groupKey)
      else next.add(groupKey)
      return next
    })
  }

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
      {!props.hideApplicationRoot ? (
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
      ) : null}
      {props.hideApplicationRoot || applicationExpanded ? (
        <div className={cx('artifact-root-children', props.hideApplicationRoot && 'flat')}>
          {!props.hideDesignArtifacts ? (
            <>
              <div className={cx('artifact-section-row', 'static')}>
                <FileTextOutlined />
                <span>文档</span>
                <small>
                  {completedDocuments}/{props.designArtifacts.length}
                </small>
              </div>
              <div className={cx('artifact-tree-children', 'section-children')}>
                {props.designArtifacts.map((artifact) => {
                  const access = props.artifactAccessById[documentArtifactId(artifact.key)]
                  return (
                    <div className={cx('artifact-row-shell')} key={artifact.key}>
                      <button
                        className={cx(
                          'artifact-row',
                          artifact.status,
                          props.selectedDesignArtifactKey === artifact.key &&
                            artifact.available &&
                            access?.mode !== 'unavailable' &&
                            'selected',
                          access?.mode === 'read' && 'read-only'
                        )}
                        disabled={!artifact.available || access?.mode === 'unavailable'}
                        onClick={() => props.onDesignArtifactSelect(artifact.key)}
                        style={{ paddingLeft: 22 }}
                        title={`${artifact.label} · ${artifact.path}${access?.message ? ` · ${access.message}` : ''}`}
                        type="button"
                      >
                        <FileTextOutlined />
                        <span className={cx('artifact-label')}>{artifact.label}</span>
                        <span
                          aria-label={artifact.status}
                          className={cx('artifact-status-dot', artifact.status)}
                        />
                      </button>
                    </div>
                  )
                })}
              </div>
            </>
          ) : null}

          {developmentArtifactsKnown ? (
            <>
              <button
                aria-expanded={pagesExpanded}
                className={cx('artifact-section-row')}
                onClick={() => setPagesExpanded((value) => !value)}
                type="button"
              >
                <CaretDownOutlined className={cx(!pagesExpanded && 'collapsed')} />
                <span>应用页面</span>
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
                      onPageSelect={props.onPageSelect}
                      pagesById={pagesById}
                      readOnly={Boolean(props.readOnly)}
                      selectedPageId={props.selectedPageId}
                    />
                  ))}
                </div>
              ) : null}

              {props.apiContracts.length > 0 ? (
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
              ) : null}
              {props.apiContracts.length > 0 && apisExpanded ? (
                <div className={cx('artifact-tree-children', 'section-children')}>
                  {props.apiContracts.map((contract) => {
                    const expanded = expandedContracts.has(contract.id)
                    const completed = contract.endpoints.filter((endpoint, index) => {
                      const endpointId = endpoint.id || String(index + 1)
                      const apiContractId = endpoint.apiContractId || contract.id
                      return (
                        props.artifactStatusById[endpointArtifactId(apiContractId, endpointId)] ===
                        'completed'
                      )
                    }).length
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
                              const status =
                                props.artifactStatusById[
                                  endpointArtifactId(apiContractId, endpointId)
                                ] || 'not-started'
                              const access =
                                props.artifactAccessById[
                                  endpointArtifactId(apiContractId, endpointId)
                                ]
                              const requestsWorkflow =
                                status === 'not-started' &&
                                !props.readOnly &&
                                access?.mode === 'write'
                              return (
                                <div className={cx('artifact-row-shell')} key={endpointKey}>
                                  <button
                                    className={cx(
                                      'artifact-row',
                                      status,
                                      props.selectedApiEndpointKey === endpointKey &&
                                        props.showDevelopmentTasks &&
                                        access?.mode !== 'unavailable' &&
                                        'selected',
                                      access?.mode === 'read' && 'read-only'
                                    )}
                                    onClick={() =>
                                      requestsWorkflow
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
                                      !props.showDevelopmentTasks ||
                                      access?.mode === 'unavailable' ||
                                      access?.reason === 'phase-locked'
                                    }
                                    style={{ paddingLeft: 36 }}
                                    title={`${label}${access?.message ? ` · ${access.message}` : ''}`}
                                    type="button"
                                  >
                                    <ApiOutlined />
                                    <code className={cx('artifact-label')}>{label}</code>
                                    <span
                                      aria-label={status}
                                      className={cx('artifact-status-dot', status)}
                                    />
                                  </button>
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

              {/* 应用API分区头与应用页面保持一致：第一级只有折叠箭头，不带业务图标。 */}
              <button
                aria-expanded={appApisExpanded}
                className={cx('artifact-section-row')}
                onClick={() => setAppApisExpanded((value) => !value)}
                type="button"
              >
                <CaretDownOutlined className={cx(!appApisExpanded && 'collapsed')} />
                <span>应用API</span>
                <small>
                  {completedAppApis}/{props.appApis.length}
                </small>
              </button>
              {appApisExpanded ? (
                <div className={cx('artifact-tree-children', 'section-children')}>
                  {props.appApis.length === 0 ? (
                    <div
                      className={cx('artifact-branch-row', 'static')}
                      style={{ paddingLeft: 22 }}
                    >
                      <CaretRightOutlined />
                      <span>请先在需求说明书的API契约中定义接口</span>
                    </div>
                  ) : (
                    // 接口即产物：应用API按业务模块（页面菜单）分组呈现，与页面目录同一套业务视角。
                    appApiGroups.map((group) => {
                      const expanded = !collapsedApiGroups.has(group.key)
                      const groupCompleted = group.apis.filter(
                        (object) =>
                          props.artifactStatusById[appApiArtifactId(object.id)] === 'completed'
                      ).length
                      return (
                        <div className={cx('artifact-tree-node')} key={group.key}>
                          <button
                            aria-expanded={expanded}
                            className={cx('artifact-branch-row')}
                            onClick={() => toggleAppApiGroup(group.key)}
                            style={{ paddingLeft: 22 }}
                            type="button"
                          >
                            <CaretDownOutlined className={cx(!expanded && 'collapsed')} />
                            <FolderOutlined />
                            <span>{group.label}</span>
                            <small>
                              {groupCompleted}/{group.apis.length}
                            </small>
                          </button>
                          {expanded ? (
                            <div className={cx('artifact-tree-children')}>
                              {group.apis.map((object) => (
                                <div className={cx('artifact-row-shell')} key={object.id}>
                                  <button
                                    className={cx(
                                      'artifact-row',
                                      props.selectedAppApiId === object.id && 'selected'
                                    )}
                                    onClick={() => props.onAppApiSelect?.(object.id)}
                                    style={{ paddingLeft: 36 }}
                                    title={`${object.name} · ${object.method} ${object.path}`}
                                    type="button"
                                  >
                                    <ApiOutlined />
                                    <span className={cx('artifact-label')}>{object.name}</span>
                                    <span
                                      aria-label={
                                        props.artifactStatusById[appApiArtifactId(object.id)] ||
                                        'not-started'
                                      }
                                      className={cx(
                                        'artifact-status-dot',
                                        props.artifactStatusById[appApiArtifactId(object.id)] ||
                                          'not-started'
                                      )}
                                    />
                                  </button>
                                </div>
                              ))}
                            </div>
                          ) : null}
                        </div>
                      )
                    })
                  )}
                </div>
              ) : null}
            </>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}

export type DevelopmentArtifactTreeProps = {
  apiContracts: DevelopmentPlanningApiContract[]
  applicationName: string
  artifactStatusById: Record<string, WorkbenchArtifactStatus>
  appApis: AppApi[]
  onApiEndpointSelect: (target: {
    apiContractId: string
    endpointId: string
    endpointKey: string
    label: string
  }) => void
  onAppApiSelect?: (objectId: string) => void
  onPageSelect: (page: DevelopmentPlanningPageOption) => void
  pages: DevelopmentPlanningPageOption[]
  pageTree: DevelopmentPlanningPageTreeNode[]
  selectedApiEndpointKey: string
  selectedAppApiId?: string
  selectedPageId: string
}

/** 开发产物目录只展示应用页面和应用API一级节点，接口的契约与绑定明细留在应用API开发面板。 */
export function DevelopmentArtifactTree({
  apiContracts,
  applicationName,
  artifactStatusById,
  appApis,
  onApiEndpointSelect,
  onAppApiSelect,
  onPageSelect,
  pages,
  pageTree,
  selectedApiEndpointKey,
  selectedAppApiId,
  selectedPageId
}: DevelopmentArtifactTreeProps): ReactElement {
  const artifactAccessById = useMemo<Record<string, WorkbenchArtifactAccess>>(() => {
    const editableAccess: WorkbenchArtifactAccess = {
      mode: 'write',
      reason: 'editable',
      message: '正式写入由当前 Workflow 的任务范围控制'
    }
    const access: Record<string, WorkbenchArtifactAccess> = {}
    pages.forEach((page) => {
      access[pageArtifactId(page.pageId)] = editableAccess
    })
    apiContracts.forEach((contract) => {
      contract.endpoints.forEach((endpoint, index) => {
        const apiContractId = endpoint.apiContractId || contract.id
        const endpointId = endpoint.id || String(index + 1)
        access[endpointArtifactId(apiContractId, endpointId)] = editableAccess
      })
    })
    appApis.forEach((object) => {
      access[appApiArtifactId(object.id)] = editableAccess
    })
    return access
  }, [apiContracts, appApis, pages])

  return (
    <ArtifactNavigation
      apiContracts={[]}
      applicationName={applicationName}
      appApis={appApis}
      artifactAccessById={artifactAccessById}
      artifactStatusById={artifactStatusById}
      designArtifacts={[]}
      hideApplicationRoot
      hideDesignArtifacts
      onApiEndpointSelect={onApiEndpointSelect}
      onAppApiSelect={onAppApiSelect}
      onCreateDocumentTask={() => undefined}
      onCreateEndpointTask={() => undefined}
      onCreatePageTask={() => undefined}
      onDesignArtifactSelect={() => undefined}
      onPageSelect={onPageSelect}
      pages={pages}
      pageTree={pageTree}
      readOnly
      selectedApiEndpointKey={selectedApiEndpointKey}
      selectedAppApiId={selectedAppApiId}
      selectedPageId={selectedPageId}
      showDevelopmentTasks
    />
  )
}
