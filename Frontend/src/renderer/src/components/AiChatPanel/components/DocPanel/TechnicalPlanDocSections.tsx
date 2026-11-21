import {
  ApiOutlined,
  ArrowRightOutlined,
  BranchesOutlined,
  DatabaseOutlined,
  RightOutlined
} from '@ant-design/icons'
import { Table, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useMemo } from 'react'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import {
  asRecord,
  authenticationLabel,
  fieldLabel,
  methodClass,
  recordItems,
  stringItems,
  textValue,
  type JsonRecord
} from './TechnicalPlanDocPanelData'
import { EndpointInspector } from './TechnicalPlanEndpointInspector'

const { Text } = Typography

/** 变更徽标状态，与需求文档 `ChangeStatus` 同一套语义与文案。 */
export type TechnicalPlanChangeStatus = 'added' | 'modified' | 'unchanged' | 'deleted'

/** 徽标文案映射：与需求文档（草稿）完全一致。 */
const CHANGE_BADGE_LABEL: Record<TechnicalPlanChangeStatus, string> = {
  added: '新增',
  modified: '有变更',
  unchanged: '未变更',
  // 技术规划里它还没有被真正清理掉（开发阶段才会删除代码），因此叫"待删除"。
  deleted: '待删除'
}

/** 徽标悬停说明，与需求文档（草稿）完全一致。 */
const CHANGE_BADGE_TITLE: Record<TechnicalPlanChangeStatus, string> = {
  added: '本轮迭代新增',
  modified: '本轮迭代有变更（与基线版本不同）',
  unchanged: '本轮迭代未变更，继承自基线',
  deleted: '本轮迭代删除'
}

/** 技术规划 diff 使用的中文种类 → 徽标状态。 */
function statusFromKind(kind: string): TechnicalPlanChangeStatus {
  if (kind === '新增') return 'added'
  if (kind === '删除') return 'deleted'
  return 'modified'
}

/** 渲染变更徽标；复用需求文档（草稿）的 `requirement-doc-change-badge` 样式，保证观感一致。 */
export function TechnicalPlanChangeBadge({
  status
}: {
  status?: TechnicalPlanChangeStatus
}): ReactElement | null {
  if (!status) return null
  return (
    <span
      className={cx('requirement-doc-change-badge', `is-${status}`)}
      title={CHANGE_BADGE_TITLE[status]}
    >
      {CHANGE_BADGE_LABEL[status]}
    </span>
  )
}

/** 按"有变更标记 → 对应种类，否则按基线判断是否继承"解析徽标状态。 */
function resolveStatus(
  kind: string | undefined,
  hasBaseline: boolean | undefined,
  /** 产物自带的 `_change_status`：后端已判定好的变更类型（含级联删除）。 */
  ownStatus?: unknown
): TechnicalPlanChangeStatus | undefined {
  // 优先用 diff 的结论；diff 没提及（例如"已删除"的页面不再参与内容比较）时，
  // 退回产物自带的标记 —— 否则删除页会被误显示成"继承自基线"。
  if (kind) return statusFromKind(kind)
  const declared = String(ownStatus || '').trim()
  if (
    declared === 'added' ||
    declared === 'modified' ||
    declared === 'unchanged' ||
    declared === 'deleted'
  ) {
    return declared
  }
  return hasBaseline ? 'unchanged' : undefined
}

export type SectionKey =
  | 'architecture'
  | 'entities'
  | 'api-contracts'
  | 'page-bindings'
  | 'authorization'

type SectionProps = {
  sectionKey: SectionKey
  changeKinds?: Record<string, string>
  /** 是否存在基线（跨迭代场景）；首轮创建时为 false，不分区。 */
  hasBaseline?: boolean
}

/** 渲染三层架构摘要，完整展示可自动换行的说明。 */
export function ArchitectureSection({
  architecture,
  changeKinds,
  hasBaseline,
  sectionKey
}: { architecture: JsonRecord } & SectionProps): ReactElement {
  const layers = [
    ['前端', textValue(architecture.frontend, '待补充')],
    ['后端', textValue(architecture.backend, '待补充')],
    ['数据库', textValue(architecture.data, '待补充')]
  ]
  // 架构是单对象：优先用 diff 给出的变更类型，没有变更但存在基线时标"继承自基线"。
  // `changeKinds` 里已优先放入产物自带的 `_change_status`（见 `TechnicalPlanDocPanel`），
  // 因此本轮需求没动架构、只是模型重写了叙述时，这里会如实显示"继承自基线"。
  const archChange = changeKinds?.['architecture']
  return (
    <section
      aria-label="架构"
      className={cx('technical-plan-section')}
      id={`technical-plan-panel-${sectionKey}`}
      role="tabpanel"
    >
      <div className={cx('technical-plan-section-title')}>
        <BranchesOutlined /> <span>技术架构</span>
        <TechnicalPlanChangeBadge status={resolveStatus(archChange, hasBaseline)} />
      </div>
      <div className={cx('technical-plan-architecture')}>
        {layers.map(([label, value], index) => (
          <div className={cx('technical-plan-architecture-layer')} key={label}>
            <span>{label}</span>
            <p className={cx('technical-plan-architecture-description')}>{value}</p>
            {index < layers.length - 1 ? <RightOutlined aria-hidden="true" /> : null}
          </div>
        ))}
      </div>
    </section>
  )
}

type EntityFieldRow = {
  key: string
  label: string
  name: string
  type: string
  required: boolean
}

const entityFieldColumns: ColumnsType<EntityFieldRow> = [
  {
    dataIndex: 'label',
    key: 'label',
    title: '字段说明',
    width: '34%',
    render: (label) => <strong>{label}</strong>
  },
  {
    dataIndex: 'name',
    key: 'name',
    title: '字段名',
    width: '30%',
    render: (name) => <code>{name}</code>
  },
  {
    dataIndex: 'type',
    key: 'type',
    title: '类型',
    width: '22%',
    render: (type) => <span>{type}</span>
  },
  {
    dataIndex: 'required',
    key: 'required',
    title: '必填',
    width: 52,
    render: (required) => (required ? <em>必填</em> : null)
  }
]

/** 渲染实体字段表，突出类型、必填状态和字段说明。 */
export function EntitiesSection({
  entities,
  changeKinds,
  hasBaseline,
  sectionKey
}: { entities: JsonRecord[] } & SectionProps): ReactElement {
  // 按 changeKinds 分两组：变更的（新增/修改/删除）在上，未变更的（继承自基线）在下。
  const withStatus = entities.map((entity, index) => {
    const id = textValue(entity.id, `entity-${index}`)
    const kind = changeKinds?.[`entities:${id}`]
    return { entity, id, index, kind, isChanged: Boolean(kind) }
  })
  const changed = withStatus.filter((e) => e.isChanged)
  const unchanged = hasBaseline ? withStatus.filter((e) => !e.isChanged) : []
  const showGroups = changed.length > 0 && unchanged.length > 0

  const renderEntity = (e: typeof withStatus[number]): ReactElement => {
    const fields = recordItems(e.entity.fields)
    return (
      <article className={cx('technical-plan-entity-card')} key={e.id}>
        <div className={cx('technical-plan-entity-heading')}>
          <div>
            <strong>{textValue(e.entity.name, `实体 ${e.index + 1}`)}</strong>
            <code>{textValue(e.entity.id)}</code>
          </div>
          <span>{fields.length} 字段</span>
          <TechnicalPlanChangeBadge status={resolveStatus(e.kind, hasBaseline, e.entity._change_status)} />
        </div>
        {textValue(e.entity.description) ? (
          <Text type="secondary">{textValue(e.entity.description)}</Text>
        ) : null}
        <Table
          className={cx('technical-plan-field-table')}
          columns={entityFieldColumns}
          dataSource={fields.map(
            (field, fieldIndex): EntityFieldRow => ({
              key: textValue(field.name, `field-${fieldIndex}`),
              label: fieldLabel(field, `字段 ${fieldIndex + 1}`),
              name: textValue(field.name),
              type: textValue(field.type, 'text'),
              required: Boolean(field.required)
            })
          )}
          pagination={false}
          rowKey="key"
          size="small"
        />
      </article>
    )
  }

  return (
    <section
      aria-label="实体"
      className={cx('technical-plan-section')}
      id={`technical-plan-panel-${sectionKey}`}
      role="tabpanel"
    >
      <div className={cx('technical-plan-section-title')}>
        <DatabaseOutlined /> <span>实体模型</span>
      </div>
      <div className={cx('technical-plan-entity-list')}>
        {!entities.length ? (
          <Text type="secondary">暂无实体定义</Text>
        ) : showGroups ? (
          <>
            <div className={cx('requirement-doc-page-group-title')}>
              <span className={cx('requirement-doc-page-group-badge', 'is-changed')}>
                本次迭代变更（{changed.length}）
              </span>
            </div>
            {changed.map((e) => renderEntity(e))}
            <div className={cx('requirement-doc-page-group-title')}>
              <span className={cx('requirement-doc-page-group-badge', 'is-unchanged')}>
                未变更内容（{unchanged.length}）
              </span>
            </div>
            {unchanged.map((e) => renderEntity(e))}
          </>
        ) : (
          withStatus.map((e) => renderEntity(e))
        )}
      </div>
    </section>
  )
}

type ContractSectionProps = {
  contracts: JsonRecord[]
  selectedContract: JsonRecord
  selectedEndpoint: JsonRecord
  selectedContractId: string
  selectedEndpointId: string
  onContractChange: (id: string) => void
  onEndpointChange: (id: string) => void
} & SectionProps

/** 渲染 API Contract 列表和 Endpoint 选择器。 */
export function ContractSection({
  contracts,
  changeKinds,
  hasBaseline,
  selectedContract,
  selectedEndpoint,
  selectedContractId,
  selectedEndpointId,
  onContractChange,
  onEndpointChange,
  sectionKey
}: ContractSectionProps): ReactElement {
  const endpoints = recordItems(selectedContract.endpoints)
  // contracts 列表按 changeKinds 分两组：变更的在上，未变更的在下。
  const contractsWithStatus = contracts.map((contract, index) => {
    const contractId = textValue(contract.id, `contract-${index + 1}`)
    const kind = changeKinds?.[`contract:${contractId}`]
    return { contract, contractId, index, kind, isChanged: Boolean(kind) }
  })
  const changedContracts = contractsWithStatus.filter((c) => c.isChanged)
  const unchangedContracts = hasBaseline ? contractsWithStatus.filter((c) => !c.isChanged) : []
  const showContractGroups = changedContracts.length > 0 && unchangedContracts.length > 0

  // 渲染单个接口分组按钮；变更的标 kind，未变更的标"未变更"。
  const renderContract = (c: typeof contractsWithStatus[number]): ReactElement => {
    const entityIds = stringItems(c.contract.entity_ids)
    return (
      <button
        className={cx(
          'technical-plan-contract',
          c.contractId === selectedContractId && 'is-selected'
        )}
        key={c.contractId}
        onClick={() => onContractChange(c.contractId)}
        type="button"
      >
        <span className={cx('technical-plan-contract-main')}>
          <span className={cx('technical-plan-contract-title')}>
            <strong>{textValue(c.contract.name, '未命名接口分组')}</strong>
            <TechnicalPlanChangeBadge status={resolveStatus(c.kind, hasBaseline, c.contract._change_status)} />
          </span>
          <code>{textValue(c.contract.base_path, '/api/resource')}</code>
        </span>
        <span className={cx('technical-plan-contract-tags')}>
          {entityIds.slice(0, 2).map((entityId) => (
            <em className={cx('is-entity')} key={entityId}>
              {entityId}
            </em>
          ))}
          <em>{authenticationLabel(c.contract.authentication)}</em>
        </span>
      </button>
    )
  }

  return (
    <section
      aria-label="API 契约"
      className={cx('technical-plan-section')}
      id={`technical-plan-panel-${sectionKey}`}
      role="tabpanel"
    >
      <div className={cx('technical-plan-section-title')}>
        <ApiOutlined /> <span>API 契约</span>
      </div>
      <div className={cx('technical-plan-contract-workbench')}>
        <div className={cx('technical-plan-contract-column')}>
          <div className={cx('technical-plan-subtitle')}>
            <span>接口分组</span>
            <span>{contracts.length}</span>
          </div>
          <div className={cx('technical-plan-contract-list')}>
            {!contracts.length ? (
              <Text type="secondary">暂无 API 契约</Text>
            ) : showContractGroups ? (
              <>
                <div className={cx('requirement-doc-page-group-title')}>
                  <span className={cx('requirement-doc-page-group-badge', 'is-changed')}>
                    本次迭代变更（{changedContracts.length}）
                  </span>
                </div>
                {changedContracts.map((c) => renderContract(c))}
                <div className={cx('requirement-doc-page-group-title')}>
                  <span className={cx('requirement-doc-page-group-badge', 'is-unchanged')}>
                    未变更内容（{unchangedContracts.length}）
                  </span>
                </div>
                {unchangedContracts.map((c) => renderContract(c))}
              </>
            ) : (
              contractsWithStatus.map((c) => renderContract(c))
            )}
          </div>
        </div>
        <div className={cx('technical-plan-endpoint-block')}>
          <div className={cx('technical-plan-subtitle')}>
            <span>接口列表</span>
            <span>{endpoints.length}</span>
          </div>
          <div className={cx('technical-plan-endpoint-table')}>
            <div className={cx('technical-plan-endpoint-head')} aria-hidden="true">
              <span>方法</span>
              <span>名称</span>
              <span>路径</span>
              <span>说明</span>
            </div>
            <div className={cx('technical-plan-endpoint-list')}>
              {endpoints.length ? (
                endpoints.map((endpoint, index) => {
                  const endpointId = textValue(
                    endpoint.id,
                    `${selectedContractId}-endpoint-${index + 1}`
                  )
                  const method = textValue(endpoint.method, 'GET').toUpperCase()
                  const summary = textValue(endpoint.summary, '待补充接口说明')
                  const endpointStatus = resolveStatus(
                    changeKinds?.[`endpoint:${selectedContractId}:${endpointId}`],
                    hasBaseline,
                    endpoint._change_status
                  )
                  return (
                    <button
                      className={cx(
                        'technical-plan-endpoint',
                        endpointId === selectedEndpointId && 'is-selected'
                      )}
                      key={endpointId}
                      onClick={() => onEndpointChange(endpointId)}
                      type="button"
                    >
                      <span className={cx('technical-plan-method', methodClass(method))}>
                        {method}
                      </span>
                      <span className={cx('technical-plan-endpoint-name')}>
                        {textValue(endpoint.name, '未命名接口')}
                      </span>
                      <code>{textValue(endpoint.path, '/')}</code>
                      {/* 说明与变更徽标同占最后一列：说明文字占据剩余空间，徽标贴行尾，
                          不挤占方法/名称/路径三列。 */}
                      <span className={cx('technical-plan-endpoint-summary')}>
                        <span className={cx('technical-plan-endpoint-summary-text')} title={summary}>
                          {summary}
                        </span>
                        <TechnicalPlanChangeBadge status={endpointStatus} />
                      </span>
                    </button>
                  )
                })
              ) : (
                <div className={cx('technical-plan-endpoint-empty')}>
                  <Text type="secondary">暂无接口</Text>
                </div>
              )}
            </div>
          </div>
          {Object.keys(selectedEndpoint).length ? (
            <EndpointInspector contract={selectedContract} endpoint={selectedEndpoint} />
          ) : null}
        </div>
      </div>
    </section>
  )
}

type PageBindingItem = {
  key: string
  triggerLabel: string
  endpointLabel: string
}

type PageBindingGroup = {
  key: string
  pageName: string
  /** 页面自带的变更标记：已删除页面在 diff 里没有内容变化，只能靠它识别。 */
  status?: unknown
  bindings: PageBindingItem[]
}

/** 按页面汇总触发条件与 Endpoint，并用 ProductPlan 中的中文页面名替换 pageId。 */
function pageBindingGroups(
  pages: JsonRecord[],
  contracts: JsonRecord[],
  productPlan: JsonRecord
): PageBindingGroup[] {
  const endpointMap = new Map<string, JsonRecord>()
  const pageNameMap = new Map<string, string>()
  contracts.forEach((contract) =>
    recordItems(contract.endpoints).forEach((endpoint) => {
      const endpointId = textValue(endpoint.id)
      if (endpointId) endpointMap.set(endpointId, endpoint)
    })
  )
  recordItems(productPlan.pages).forEach((page) => {
    const pageId = textValue(page.pageId)
    const pageName = textValue(page.name)
    if (pageId && pageName) pageNameMap.set(pageId, pageName)
  })

  return pages.flatMap((page, pageIndex): PageBindingGroup[] => {
    const pageId = textValue(page.pageId, `page-${pageIndex + 1}`)
    const references = asRecord(page.references)
    const bindings = recordItems(references.endpoint_dependencies).map(
      (dependency, dependencyIndex): PageBindingItem => {
        const endpointId = textValue(dependency.endpoint_id)
        const endpoint = endpointMap.get(endpointId) || {}
        return {
          key: `${pageId}:${endpointId || dependencyIndex}`,
          triggerLabel: textValue(dependency.trigger, '未声明触发条件'),
          endpointLabel: `${textValue(endpoint.method, 'GET').toUpperCase()} ${textValue(endpoint.path, endpointId || '未解析')}`
        }
      }
    )
    return bindings.length
      ? [
          {
            key: pageId,
            pageName: pageNameMap.get(pageId) || pageId,
            status: page._change_status,
            bindings
          }
        ]
      : []
  })
}

/** 按页面分组展示中文页面名、触发条件和 Endpoint 三段调用关系。 */
export function PageBindingsSection({
  pages,
  changeKinds,
  hasBaseline,
  contracts,
  productPlan,
  sectionKey
}: {
  pages: JsonRecord[]
  contracts: JsonRecord[]
  productPlan: JsonRecord
} & SectionProps): ReactElement {
  const groups = useMemo(
    () => pageBindingGroups(pages, contracts, productPlan),
    [contracts, pages, productPlan]
  )
  // 按 changeKinds 分两组：变更的在上，未变更的在下。
  // 注意 diff 里的页面对象身份是 `pages:<technical_plan.pages[].pageId>`，
  // 而这里的分组键来自同一个 pageId —— 没有接口绑定的页面不会出现在本 tab 里，
  // 这不影响对齐：有绑定的页面必定在 technical_plan.pages 中登记过。
  const withStatus = groups.map((group) => {
    const kind = changeKinds?.[`pages:${group.key}`]
    return { group, kind, isChanged: Boolean(kind) }
  })
  const changed = withStatus.filter((g) => g.isChanged)
  const unchanged = hasBaseline ? withStatus.filter((g) => !g.isChanged) : []
  const showGroups = changed.length > 0 && unchanged.length > 0

  const renderGroup = (g: typeof withStatus[number]): ReactElement => (
    <article className={cx('technical-plan-page-binding-group')} key={g.group.key}>
      <strong className={cx('technical-plan-page-binding-name')}>
        {/* 名称单独包一层：flex 行里匿名文本会与徽标一起被弹性拉伸，
            包成元素后两者才能各自作为整体描述子并使用固定间距。 */}
        <span className={cx('technical-plan-page-binding-label')}>{g.group.pageName}</span>
        <TechnicalPlanChangeBadge status={resolveStatus(g.kind, hasBaseline, g.group.status)} />
      </strong>
      <div className={cx('technical-plan-page-binding-rows')}>
        {g.group.bindings.map((binding) => (
          <div className={cx('technical-plan-page-binding-row')} key={binding.key}>
            <span>{binding.triggerLabel}</span>
            <ArrowRightOutlined aria-hidden="true" />
            <code>{binding.endpointLabel}</code>
          </div>
        ))}
      </div>
    </article>
  )

  return (
    <section
      aria-label="页面绑定"
      className={cx('technical-plan-section')}
      id={`technical-plan-panel-${sectionKey}`}
      role="tabpanel"
    >
      <div className={cx('technical-plan-section-title')}>
        <BranchesOutlined /> <span>页面绑定</span>
      </div>
      <div className={cx('technical-plan-page-binding-list')}>
        {!groups.length ? (
          <Text type="secondary">暂无页面绑定</Text>
        ) : showGroups ? (
          <>
            <div className={cx('technical-plan-binding-group', 'is-changed')}>
              <div className={cx('requirement-doc-page-group-title')}>
                <span className={cx('requirement-doc-page-group-badge', 'is-changed')}>
                  本次迭代变更（{changed.length}）
                </span>
              </div>
              <div className={cx('technical-plan-page-binding-list')}>
                {changed.map((g) => renderGroup(g))}
              </div>
            </div>
            <div className={cx('technical-plan-binding-group', 'is-unchanged')}>
              <div className={cx('requirement-doc-page-group-title')}>
                <span className={cx('requirement-doc-page-group-badge', 'is-unchanged')}>
                  未变更内容（{unchanged.length}）
                </span>
              </div>
              <div className={cx('technical-plan-page-binding-list')}>
                {unchanged.map((g) => renderGroup(g))}
              </div>
            </div>
          </>
        ) : (
          withStatus.map((g) => renderGroup(g))
        )}
      </div>
    </section>
  )
}
