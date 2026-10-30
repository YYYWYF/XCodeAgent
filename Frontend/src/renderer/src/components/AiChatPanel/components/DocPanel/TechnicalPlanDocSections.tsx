import { ApiOutlined, BranchesOutlined, DatabaseOutlined, RightOutlined } from '@ant-design/icons'
import { Table, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
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
import TechnicalPlanAgentInvocation from './TechnicalPlanAgentInvocation'

const { Text } = Typography

export type SectionKey =
  | 'architecture'
  | 'entities'
  | 'api-contracts'
  | 'page-bindings'
  | 'authorization'
  | 'agents'

type SectionProps = {
  sectionKey: SectionKey
}

/** 渲染三层架构摘要，完整展示可自动换行的说明。 */
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
            <span>接口分组</span>
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
                      <strong>{textValue(contract.name, '未命名接口分组')}</strong>
                      <code>{textValue(contract.base_path, '/api/resource')}</code>
                    </span>
                    <span className={cx('technical-plan-contract-tags')}>
                      {entityIds.slice(0, 2).map((entityId) => (
                        <em className={cx('is-entity')} key={entityId}>
                          {entityId}
                        </em>
                      ))}
                      <em>
                        {contract.invocation
                          ? 'AG-UI / SSE'
                          : authenticationLabel(contract.authentication)}
                      </em>
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
                      <span title={summary}>{summary}</span>
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

/** 渲染所选 Endpoint 的参数、Schema、错误码和字段血缘检查器。 */
export function EndpointInspector({
  contract,
  endpoint
}: {
  contract: JsonRecord
  endpoint: JsonRecord
}): ReactElement {
  if (contract.invocation) {
    return (
      <TechnicalPlanAgentInvocation
        agentId={textValue(contract.agentId)}
        pageIds={contract.pageIds}
      />
    )
  }
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
        <strong className={cx('technical-plan-endpoint-name')}>
          {textValue(endpoint.name, '未命名接口')}
        </strong>
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
