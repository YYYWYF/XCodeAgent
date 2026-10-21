import {
  CaretDownOutlined,
  FilterOutlined,
  LockOutlined,
  SearchOutlined
} from '@ant-design/icons'
import { Input, Switch, Typography } from 'antd'
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
import { developmentCompletedCount } from '../../../../developmentArtifacts'
import {
  collectRelatedKeys,
  collectVisibleKeys,
  containsMenuKey,
  pageTreeItems
} from './outlineUtils'

const { Text } = Typography

export type ApplicationOutlineProps = {
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

/** 渲染开发产物列表，提供搜索、筛选、分组展开与产物浏览入口。 */
export default function ApplicationOutline({
  developmentArtifacts,
  currentBranch,
  apiContracts = [],
  onApiEndpointSelect,
  onPageSelect,
  outlineLocked,
  pages,
  pageTree,
  selectedApiEndpointKey,
  selectedPageId
}: ApplicationOutlineProps): ReactElement {
  const [outlineQuery, setOutlineQuery] = useState('')
  const [pagesExpanded, setPagesExpanded] = useState(true)
  const [apiExpanded, setApiExpanded] = useState(true)
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
        aria-label={outlineLocked ? '页面产物暂不可操作，API 仍可选择' : '开发产物'}
        className={cx('session-outline-lock-shell')}
      >
        <div className={cx('session-outline-content')}>
          <Input
            allowClear
            aria-label="搜索页面或接口"
            className={cx('session-search')}
            onChange={(event) => setOutlineQuery(event.target.value)}
            placeholder="搜索页面或接口"
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
                <span className={cx('development-count')}>
                  {developmentCompletedCount(
                    pages.map((page) => developmentArtifacts?.pages[page.pageId])
                  )}
                  /{pages.length}
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
                <span className={cx('development-count')}>
                  {developmentCompletedCount(
                    apiContracts.flatMap((contract) =>
                      contract.endpoints.map(
                        (endpoint) =>
                          developmentArtifacts?.endpoints[endpoint.apiContractId || contract.id]?.[
                            endpoint.id
                          ]
                      )
                    )
                  )}
                  /{apiContracts.reduce((total, contract) => total + contract.endpoints.length, 0)}
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
                      project_plan.json 的 api_contracts 中暂无接口
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
