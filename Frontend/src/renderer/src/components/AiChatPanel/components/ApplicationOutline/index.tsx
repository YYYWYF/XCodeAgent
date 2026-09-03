import {
  ApiOutlined,
  CaretDownOutlined,
  DatabaseOutlined,
  FilterOutlined,
  LockOutlined,
  SearchOutlined
} from '@ant-design/icons'
import { Input, Switch, Typography } from 'antd'
import type { ReactElement } from 'react'
import { useMemo, useState } from 'react'
import type {
  ApplicationMenuItem,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningEntityOption,
  DevelopmentPlanningPageOption,
  DevelopmentPlanningPageTreeNode
} from '../../../../typings'
import { cx } from '../../../../utils'
import { apiEndpointDisplayPath } from '../../utils'
import './ApplicationOutline.less'
import { OutlineRow } from './outlineHelpers'
import {
  apiEndpointSelectionKey,
  collectRelatedKeys,
  collectVisibleKeys,
  containsMenuKey,
  pageTreeItems
} from './outlineUtils'

const { Text } = Typography

export type ApplicationOutlineProps = {
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

/** 渲染开发产物列表，提供搜索、筛选、分组展开与产物浏览入口。 */
export default function ApplicationOutline({
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
  const [onlyRelated, setOnlyRelated] = useState(false)
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
        aria-label={outlineLocked ? '页面产物暂不可操作，API 与实体仍可选择' : '开发产物'}
        className={cx('session-outline-lock-shell')}
      >
        <div className={cx('session-outline-content')}>
          <Input
            allowClear
            aria-label="搜索页面、接口或实体"
            className={cx('session-search')}
            onChange={(event) => setOutlineQuery(event.target.value)}
            placeholder="搜索页面、接口或实体"
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
                <span>页面</span>
              </button>
              {pagesExpanded ? (
                <div className={cx('outline-tree')}>
                  {pageItems
                    .filter((item) => visibleKeys.has(item.key))
                    .map((item) => (
                      <OutlineRow
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
                    <div className={cx('outline-empty')}>当前计划 pages 中暂无页面</div>
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
                <span>接口</span>
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
                                  </button>
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
                <span>实体</span>
              </button>
              {entitiesExpanded ? (
                <div className={cx('entity-group')}>
                  {visibleEntities.map((entity) => {
                    return (
                      <div className={cx('entity-node')} key={entity.id}>
                        <button
                          aria-current={selectedEntityId === entity.id ? 'true' : undefined}
                          className={cx('entity-row', selectedEntityId === entity.id && 'selected')}
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
                            </span>
                            <span className={cx('entity-meta')}>{entity.id}</span>
                          </span>
                        </button>
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
    </div>
  )
}
