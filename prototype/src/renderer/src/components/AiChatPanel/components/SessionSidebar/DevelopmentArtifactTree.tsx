import {
  ApiOutlined,
  AppstoreOutlined,
  CaretDownOutlined,
  CaretRightOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  FolderOutlined
} from '@ant-design/icons'
import type { ReactElement } from 'react'
import { useMemo, useState } from 'react'
import type { WorkspaceDocKey } from '../../types'
import type {
  DevelopmentPlanningApiContract,
  DevelopmentPlanningEntity,
  DevelopmentPlanningPageOption,
  DevelopmentPlanningPageTreeNode
} from '../../../../typings'
import { cx } from '../../../../utils'
import { apiEndpointDisplayPath } from '../../utils'
import { documentArtifactId, entityArtifactId, endpointArtifactId, pageArtifactId } from '../../../../workbenchDomain'
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

/** 递归渲染页面菜单树，菜单节点只组织层级，页面叶子承载产物操作。 */
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
          selectedPageId === pageId && artifactsAvailable && access?.mode !== 'unavailable' && 'selected',
          access?.mode === 'read' && 'read-only'
        )}
        onClick={() => (requestsWorkflow ? onCreatePageTask(page) : onPageSelect(page))}
        disabled={
          !artifactsAvailable ||
          access?.mode === 'unavailable' ||
          access?.reason === 'phase-locked'
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
  artifactAccessById: Record<string, WorkbenchArtifactAccess>
  artifactStatusById: Record<string, WorkbenchArtifactStatus>
  designArtifacts: DesignArtifactItem[]
  entities: DevelopmentPlanningEntity[]
  /** 开发产物工作区不展示需求分析/项目规划阶段文档，只复用原有开发目录树。 */
  hideDesignArtifacts?: boolean
  /** 开发产物目录不展示应用根节点，页面/接口/实体直接作为一级分组。 */
  hideApplicationRoot?: boolean
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
  showDevelopmentTasks: boolean
}

/** 渲染以应用为根的完整产物树，页面和接口分别保留业务分组。 */
function ArtifactNavigation(props: ArtifactNavigationProps): ReactElement {
  const [applicationExpanded, setApplicationExpanded] = useState(true)
  const [pagesExpanded, setPagesExpanded] = useState(true)
  const [apisExpanded, setApisExpanded] = useState(true)
  const [entitiesExpanded, setEntitiesExpanded] = useState(true)
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
        return props.artifactStatusById[endpointArtifactId(apiContractId, endpointId)] === 'completed'
      }).length,
    0
  )
  const completedEntities = 0
  const completedDocuments = props.designArtifacts.filter(
    (artifact) => artifact.status === 'completed'
  ).length
  // 页面、接口和实体只有在项目计划确认保存后才进入正式产物树。
  const developmentArtifactsKnown = props.showDevelopmentTasks
  const completedTotal = completedDocuments + completedPages + completedEndpoints + completedEntities
  const artifactTotal =
    props.designArtifacts.length +
    (developmentArtifactsKnown
      ? props.pages.length + endpointTotal + props.entities.length
      : 0)

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
          {!props.hideDesignArtifacts ? <>
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
          </> : null}

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
                      onPageSelect={props.onPageSelect}
                      pagesById={pagesById}
                      readOnly={Boolean(props.readOnly)}
                      selectedPageId={props.selectedPageId}
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
                                status === 'not-started' && !props.readOnly && access?.mode === 'write'
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
                                    <span aria-label={status} className={cx('artifact-status-dot', status)} />
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

              <button
                aria-expanded={entitiesExpanded}
                className={cx('artifact-section-row')}
                onClick={() => setEntitiesExpanded((value) => !value)}
                type="button"
              >
                <CaretDownOutlined className={cx(!entitiesExpanded && 'collapsed')} />
                <span>实体</span>
                <small>{completedEntities}/{props.entities.length}</small>
              </button>
              {entitiesExpanded ? (
                props.entities.length === 0 ? (
                  <div className={cx('artifact-tree-children', 'section-children')}>
                    <div
                      aria-label="实体定义，敬请期待"
                      className={cx('artifact-branch-row', 'static')}
                      style={{ paddingLeft: 22 }}
                    >
                      <CaretRightOutlined />
                      <FolderOutlined />
                      <span>实体定义</span>
                      <small>敬请期待</small>
                    </div>
                  </div>
                ) : (
                  <div className={cx('artifact-tree-children', 'section-children')}>
                    {props.entities.map((entity) => {
                    const entityId = entityArtifactId(entity.entityId)
                    const developmentStatus = props.artifactStatusById[entityId] || 'not-started'
                      return (
                        <div className={cx('artifact-row-shell')} key={entity.entityId}>
                          <button
                          aria-label={`${entity.label}实体，${developmentStatus}`}
                            className={cx('artifact-row', developmentStatus, 'read-only')}
                            disabled
                            style={{ paddingLeft: 22 }}
                            title={`${entity.purpose} · 实体仅作概念提示，暂不生成具体产物`}
                            type="button"
                          >
        <DatabaseOutlined />
                            <span className={cx('artifact-label')}>{entity.label}</span>
                            <span className={cx('artifact-entity-placeholder')}>占位</span>
                          <span aria-label={developmentStatus} className={cx('artifact-status-dot', developmentStatus)} />
                          </button>
                        </div>
                      )
                    })}
                  </div>
                )
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
  entities: DevelopmentPlanningEntity[]
  onApiEndpointSelect: (target: {
    apiContractId: string
    endpointId: string
    endpointKey: string
    label: string
  }) => void
  onPageSelect: (page: DevelopmentPlanningPageOption) => void
  pages: DevelopmentPlanningPageOption[]
  pageTree: DevelopmentPlanningPageTreeNode[]
  selectedApiEndpointKey: string
  selectedPageId: string
}

/** 复用旧版产物视图，只保留开发阶段需要的应用、页面、接口与实体目录树。 */
export function DevelopmentArtifactTree({
  apiContracts,
  applicationName,
  artifactStatusById,
  entities,
  onApiEndpointSelect,
  onPageSelect,
  pages,
  pageTree,
  selectedApiEndpointKey,
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
    entities.forEach((entity) => {
      access[entityArtifactId(entity.entityId)] = editableAccess
    })
    return access
  }, [apiContracts, entities, pages])

  return (
    <ArtifactNavigation
      apiContracts={apiContracts}
      applicationName={applicationName}
      artifactAccessById={artifactAccessById}
      artifactStatusById={artifactStatusById}
      designArtifacts={[]}
      entities={entities}
      hideApplicationRoot
      hideDesignArtifacts
      onApiEndpointSelect={onApiEndpointSelect}
      onCreateDocumentTask={() => undefined}
      onCreateEndpointTask={() => undefined}
      onCreatePageTask={() => undefined}
      onDesignArtifactSelect={() => undefined}
      onPageSelect={onPageSelect}
      pages={pages}
      pageTree={pageTree}
      readOnly
      selectedApiEndpointKey={selectedApiEndpointKey}
      selectedPageId={selectedPageId}
      showDevelopmentTasks
    />
  )
}
