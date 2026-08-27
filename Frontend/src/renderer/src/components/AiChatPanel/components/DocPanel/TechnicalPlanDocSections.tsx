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
  parameterSummary,
  recordItems,
  schemaFor,
  stringItems,
  textValue,
  type JsonRecord
} from './TechnicalPlanDocPanelData'
import { SchemaTree } from './TechnicalPlanSchemaTree'

const { Text } = Typography

export type SectionKey = 'architecture' | 'entities' | 'api-contracts' | 'page-bindings' | 'authorization'

type SectionProps = {
  sectionKey: SectionKey
}

/** 渲染三层架构的紧凑摘要，不扩大右侧文档的纵向占用。 */
export function ArchitectureSection({
  architecture,
  sectionKey
}: { architecture: JsonRecord } & SectionProps): ReactElement {
  const layers = [
    ['前端', textValue(architecture.frontend, '待补充')],
    ['后端', textValue(architecture.backend, '待补充')],
    ['数据库', textValue(architecture.data, '待补充')]
  ]
  return (
    <section
      aria-label="架构"
      className={cx('technical-plan-section')}
      id={`technical-plan-panel-${sectionKey}`}
      role="tabpanel"
    >
      <div className={cx('technical-plan-section-title')}>
        <BranchesOutlined /> <span>技术架构</span>
      </div>
      <div className={cx('technical-plan-architecture')}>
        {layers.map(([label, value], index) => (
          <div className={cx('technical-plan-architecture-layer')} key={label}>
            <span>{label}</span>
            <strong>{value}</strong>
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
  sectionKey
}: { entities: JsonRecord[] } & SectionProps): ReactElement {
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
        {entities.length ? (
          entities.map((entity, index) => {
            const fields = recordItems(entity.fields)
            return (
              <article
                className={cx('technical-plan-entity-card')}
                key={textValue(entity.id, `entity-${index}`)}
              >
                <div className={cx('technical-plan-entity-heading')}>
                  <div>
                    <strong>{textValue(entity.name, `实体 ${index + 1}`)}</strong>
                    <code>{textValue(entity.id)}</code>
                  </div>
                  <span>{fields.length} 字段</span>
                </div>
                {textValue(entity.description) ? (
                  <Text type="secondary">{textValue(entity.description)}</Text>
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
          })
        ) : (
          <Text type="secondary">暂无实体定义</Text>
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
  selectedContract,
  selectedEndpoint,
  selectedContractId,
  selectedEndpointId,
  onContractChange,
  onEndpointChange,
  sectionKey
}: ContractSectionProps): ReactElement {
  const endpoints = recordItems(selectedContract.endpoints)
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
            <span>Base_Path</span>
            <span>{contracts.length}</span>
          </div>
          <div className={cx('technical-plan-contract-list')}>
            {contracts.length ? (
              contracts.map((contract, index) => {
                const contractId = textValue(contract.id, `contract-${index + 1}`)
                const entityIds = stringItems(contract.entity_ids)
                return (
                  <button
                    className={cx(
                      'technical-plan-contract',
                      contractId === selectedContractId && 'is-selected'
                    )}
                    key={contractId}
                    onClick={() => onContractChange(contractId)}
                    type="button"
                  >
                    <span className={cx('technical-plan-contract-main')}>
                      <strong>{textValue(contract.base_path, '/api/resource')}</strong>
                    </span>
                    <span className={cx('technical-plan-contract-tags')}>
                      {entityIds.slice(0, 2).map((entityId) => (
                        <em className={cx('is-entity')} key={entityId}>
                          {entityId}
                        </em>
                      ))}
                      <em>{authenticationLabel(contract.authentication)}</em>
                    </span>
                  </button>
                )
              })
            ) : (
              <Text type="secondary">暂无 API 契约</Text>
            )}
          </div>
        </div>
        <div className={cx('technical-plan-endpoint-block')}>
          <div className={cx('technical-plan-subtitle')}>
            <span>Endpoints</span>
            <span>{endpoints.length}</span>
          </div>
          <div className={cx('technical-plan-endpoint-table')}>
            <div className={cx('technical-plan-endpoint-head')} aria-hidden="true">
              <span>方法</span>
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
                      <code>{textValue(endpoint.path, '/')}</code>
                      <span>{textValue(endpoint.summary, '待补充接口说明')}</span>
                    </button>
                  )
                })
              ) : (
                <Text type="secondary">暂无 Endpoint</Text>
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

/** 渲染所选 Endpoint 的参数、Schema、错误码和字段血缘检查器。 */
export function EndpointInspector({
  contract,
  endpoint
}: {
  contract: JsonRecord
  endpoint: JsonRecord
}): ReactElement {
  const responseRef = textValue(endpoint.response_schema_ref)
  const requestRef = textValue(endpoint.request_schema_ref)
  const schemaRef = responseRef || requestRef
  const schema = schemaFor(contract, schemaRef)
  const schemas = asRecord(contract.schemas)
  const errorCodes = stringItems(endpoint.error_codes)
  return (
    <div className={cx('technical-plan-inspector')}>
      <div className={cx('technical-plan-inspector-title')}>
        <span
          className={cx(
            'technical-plan-method',
            methodClass(textValue(endpoint.method, 'GET').toUpperCase())
          )}
        >
          {textValue(endpoint.method, 'GET').toUpperCase()}
        </span>
        <code>{textValue(endpoint.path, '/')}</code>
      </div>
      <dl className={cx('technical-plan-inspector-grid')}>
        <div>
          <dt>鉴权</dt>
          <dd>{authenticationLabel(endpoint.authentication || contract.authentication)}</dd>
        </div>
        <div>
          <dt>参数</dt>
          <dd>{parameterSummary(endpoint)}</dd>
        </div>
        <div>
          <dt>请求</dt>
          <dd>{requestRef || '无'}</dd>
        </div>
        <div>
          <dt>响应</dt>
          <dd>{responseRef || '无'}</dd>
        </div>
        <div>
          <dt>错误码</dt>
          <dd>{errorCodes.length ? errorCodes.join('、') : '未声明'}</dd>
        </div>
      </dl>
      {schemaRef ? (
        <div className={cx('technical-plan-schema-card')}>
          <div className={cx('technical-plan-schema-heading')}>
            <strong>{responseRef ? '响应 Schema' : '请求 Schema'}</strong>
          </div>
          {Object.keys(schema).length ? (
            <SchemaTree name={schemaRef} schema={schema} schemas={schemas} />
          ) : (
            <Text type="secondary">Schema 引用暂不可解析</Text>
          )}
        </div>
      ) : null}
    </div>
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
      ? [{ key: pageId, pageName: pageNameMap.get(pageId) || pageId, bindings }]
      : []
  })
}

/** 按页面分组展示中文页面名、触发条件和 Endpoint 三段调用关系。 */
export function PageBindingsSection({
  pages,
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
        {groups.length ? (
          groups.map((group) => (
            <article className={cx('technical-plan-page-binding-group')} key={group.key}>
              <strong className={cx('technical-plan-page-binding-name')}>{group.pageName}</strong>
              <div className={cx('technical-plan-page-binding-rows')}>
                {group.bindings.map((binding) => (
                  <div className={cx('technical-plan-page-binding-row')} key={binding.key}>
                    <span>{binding.triggerLabel}</span>
                    <ArrowRightOutlined aria-hidden="true" />
                    <code>{binding.endpointLabel}</code>
                  </div>
                ))}
              </div>
            </article>
          ))
        ) : (
          <Text type="secondary">暂无页面绑定</Text>
        )}
      </div>
    </section>
  )
}
