import {
  CaretDownOutlined,
  DatabaseOutlined,
  LockOutlined,
  ReloadOutlined,
  SearchOutlined
} from '@ant-design/icons'
import { Button, Input, Typography } from 'antd'
import type { ReactElement } from 'react'
import { useMemo, useState } from 'react'
import type {
  ApplicationMenuItem,
  DevelopmentArtifacts,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningEntityOption,
  DevelopmentPlanningPageOption,
  DevelopmentPlanningPageTreeNode
} from '../../../../typings'
import { cx } from '../../../../utils'
import './ApplicationOutline.less'
import { OutlineRow } from './outlineHelpers'
import ApiOutlineGroup from './ApiOutlineGroup'
import CatalogReadNotice from './CatalogReadNotice'
import DocumentOutline from './DocumentOutline'
import type { ArtifactDocumentKey } from './artifactDocuments'
import { developmentCompletedCount } from '../../../../developmentArtifacts'
import {
  collectVisibleKeys,
  containsMenuKey,
  pageTreeItems
} from './outlineUtils'

const { Text } = Typography

export type ApplicationOutlineProps = {
  onDocumentSelect?: (key: ArtifactDocumentKey) => void
  selectedDocumentKey?: ArtifactDocumentKey
  planningCatalogStatus?: import('../../../../typings').PlanningCatalogStatus
  onPlanningCatalogRetry?: () => void
  developmentArtifacts?: DevelopmentArtifacts
  /** 当前迭代的分支名（= 版本号），用于标注产物归属。 */
  currentBranch?: string
  apiContracts: DevelopmentPlanningApiContract[]
  entities: DevelopmentPlanningEntityOption[]
  onApiEndpointSelect: (target: {
    apiContractId: string
    endpointId: string
    endpointKey: string
    label: string
  }) => void
  onEntitySelect: (entity: DevelopmentPlanningEntityOption) => void
  onPageSelect: (page: DevelopmentPlanningPageOption) => void
  outlineLocked: boolean
  pages: DevelopmentPlanningPageOption[]
  pageTree: DevelopmentPlanningPageTreeNode[]
  selectedApiEndpointKey: string
  selectedEntityId: string
  selectedPageId: string
}

/** 渲染开发产物列表，提供搜索、分组展开与产物浏览入口。 */
export default function ApplicationOutline({
  onDocumentSelect,
  selectedDocumentKey,
  planningCatalogStatus,
  onPlanningCatalogRetry,
  developmentArtifacts,
  currentBranch,
  apiContracts = [],
  entities = [],
  onApiEndpointSelect,
  onEntitySelect,
  onPageSelect,
  outlineLocked,
  pages,
  pageTree,
  selectedApiEndpointKey,
  selectedEntityId,
  selectedPageId
}: ApplicationOutlineProps): ReactElement {
  const [outlineQuery, setOutlineQuery] = useState('')
  const [pagesExpanded, setPagesExpanded] = useState(true)
  const [apiExpanded, setApiExpanded] = useState(true)
  const [entitiesExpanded, setEntitiesExpanded] = useState(true)
  const [collapsedApiContractIds, setCollapsedApiContractIds] = useState<Set<string>>(
    () => new Set()
  )
  const productAvailable = planningCatalogStatus?.productAvailable !== false
  const technicalAvailable = planningCatalogStatus?.technicalAvailable !== false
  const pagesById = useMemo(() => new Map(pages.map((page) => [page.pageId, page])), [pages])
  const pageItems = useMemo<ApplicationMenuItem[]>(
    () =>
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
          })),
    [pageTree, pages]
  )
  const selectedKey = selectedDocumentKey || selectedApiEndpointKey || selectedEntityId
    ? ''
    : containsMenuKey(pageItems, selectedPageId)
      ? selectedPageId
      : ''
  const visibleKeys = useMemo(
    () => collectVisibleKeys(pageItems, outlineQuery),
    [outlineQuery, pageItems]
  )
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
  const query = outlineQuery.trim().toLocaleLowerCase()
  const visibleEntities = entities.filter((entity) =>
    [entity.id, entity.label, entity.purpose].some((value) =>
      value.toLocaleLowerCase().includes(query)
    )
  )
  /** 独立切换一个 API contract 分组，避免多个资源同时收起或展开。 */
  const handleApiContractToggle = (contractId: string): void => {
    setCollapsedApiContractIds((current) => {
      const next = new Set(current)
      if (next.has(contractId)) next.delete(contractId)
      else next.add(contractId)
      return next
    })
  }

  return (
    <div className={cx('application-outline')}>
      <fieldset
        aria-disabled={outlineLocked}
        aria-label={outlineLocked ? '页面产物暂不可操作，API 仍可选择' : '应用产物'}
        className={cx('session-outline-lock-shell')}
      >
        <div className={cx('session-outline-content')}>
          <div className={cx('catalog-search-row')}>
            <Input
              allowClear
              aria-label="搜索文档、页面、接口或实体"
              className={cx('session-search')}
              onChange={(event) => setOutlineQuery(event.target.value)}
              placeholder="搜索文档、页面、接口或实体"
              prefix={<SearchOutlined />}
              value={outlineQuery}
            />
            <Button
              aria-label="刷新应用产物"
              className={cx('catalog-refresh')}
              disabled={planningCatalogStatus?.loading || !onPlanningCatalogRetry}
              icon={<ReloadOutlined />}
              loading={planningCatalogStatus?.loading}
              onClick={onPlanningCatalogRetry}
              title="刷新应用产物"
              type="text"
            />
          </div>
          <div className={cx('session-outline-scroll')}>
            <CatalogReadNotice status={planningCatalogStatus} onRetry={onPlanningCatalogRetry} />
            {onDocumentSelect ? (
              <DocumentOutline query={outlineQuery} selectedKey={selectedDocumentKey} onSelect={onDocumentSelect} />
            ) : null}
            <section className={cx('outline-section')}>
              <button
                aria-expanded={pagesExpanded}
                className={cx('outline-section-heading')}
                onClick={() => setPagesExpanded((current) => !current)}
                type="button"
              >
                <CaretDownOutlined className={cx(!pagesExpanded && 'collapsed')} />
                <span>页面</span>
                <span className={cx('development-count')}>
                  {productAvailable ? <>{developmentCompletedCount(
                    pages.map((page) => developmentArtifacts?.pages[page.pageId])
                  )}
                  /{pages.length}</> : '—'}
                </span>
              </button>
              {pagesExpanded ? (
                <div className={cx('outline-tree')}>
                  {pageItems
                    .filter((item) => visibleKeys.has(item.key))
                    .map((item) => (
                      <OutlineRow
                        currentBranch={currentBranch}
                        progressByPage={developmentArtifacts?.pages}
                        disabled={outlineLocked}
                        item={item}
                        key={item.key}
                        level={0}
                        onSelect={(key) => {
                          const selectedPage = pagesById.get(key)
                          if (selectedPage) onPageSelect(selectedPage)
                        }}
                        selectedKey={selectedKey}
                        visibleKeys={visibleKeys}
                      />
                    ))}
                  {pageItems.length === 0 ? (
                    <div className={cx('outline-empty')}>{productAvailable ? '当前规划中暂无页面' : '页面目录尚未读取成功'}</div>
                  ) : null}
                  {pageItems.length > 0 && visibleKeys.size === 0 ? <div className={cx('outline-empty')}>没有匹配的页面</div> : null}
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
                <span>接口</span>
                <span className={cx('development-count')}>
                  {technicalAvailable ? <>{developmentCompletedCount(
                    apiContracts.flatMap((contract) =>
                      contract.endpoints.map(
                        (endpoint) =>
                          developmentArtifacts?.endpoints[endpoint.apiContractId || contract.id]?.[
                            endpoint.id
                          ]
                      )
                    )
                  )}
                  /{apiContracts.reduce((total, contract) => total + contract.endpoints.length, 0)}</> : '—'}
                </span>
              </button>
              {apiExpanded ? (
                <div className={cx('api-group')}>
                  {visibleApiContracts.map((contract) => (
                    <ApiOutlineGroup
                      key={contract.id}
                      contract={contract}
                      allEndpoints={
                        apiContracts.find((item) => item.id === contract.id)?.endpoints || []
                      }
                      currentBranch={currentBranch}
                      developmentArtifacts={developmentArtifacts}
                      expanded={!collapsedApiContractIds.has(contract.id)}
                      onToggle={() => handleApiContractToggle(contract.id)}
                      onSelect={onApiEndpointSelect}
                      selectedKey={selectedApiEndpointKey}
                    />
                  ))}
                  {visibleApiContracts.length === 0 ? (
                    <div className={cx('outline-empty')}>
                      {!technicalAvailable ? '接口目录尚未读取成功' : apiContracts.length ? '没有匹配的接口' : '暂无接口'}
                    </div>
                  ) : null}
                </div>
              ) : null}
            </section>
            <section className={cx('outline-section')}>
              <button
                aria-expanded={entitiesExpanded}
                className={cx('outline-section-heading')}
                onClick={() => setEntitiesExpanded((current) => !current)}
                type="button"
              >
                <CaretDownOutlined className={cx(!entitiesExpanded && 'collapsed')} />
                <span>实体</span>
                <span className={cx('development-count')}>{technicalAvailable ? entities.length : '—'}</span>
              </button>
              {entitiesExpanded ? (
                <div className={cx('api-list')}>
                  {visibleEntities.map((entity) => (
                    <button
                      aria-current={selectedEntityId === entity.id ? 'true' : undefined}
                      aria-label={`${entity.label}，${entity.id}`}
                      className={cx('api-row', 'entity-row', selectedEntityId === entity.id && 'selected')}
                      key={entity.id}
                      onClick={() => onEntitySelect(entity)}
                      title={entity.purpose}
                      type="button"
                    >
                      <DatabaseOutlined className={cx('api-endpoint-icon')} />
                      <span className={cx('api-copy')}><strong>{entity.label}</strong></span>
                    </button>
                  ))}
                  {visibleEntities.length === 0 ? (
                    <div className={cx('outline-empty')}>
                      {!technicalAvailable ? '实体目录尚未读取成功' : entities.length === 0 ? '当前计划中暂无实体' : '没有匹配的实体'}
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
    </div>
  )
}
