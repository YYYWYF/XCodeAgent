import { ApiOutlined } from '@ant-design/icons'
import { Empty, Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import { useMemo, useState } from 'react'
import { cx } from '../../../../utils'
import type { ProjectedContract, ProjectedEndpoint } from './TechnicalPlanContractsProjection'

const { Text } = Typography

/** 方法徽标的主题色类：读操作成功色、写操作强调紫、改操作警告色、删操作危险色。 */
function methodClass(method: string): string {
  if (method === 'GET') return 'is-get'
  if (method === 'DELETE') return 'is-delete'
  if (method === 'PUT') return 'is-put'
  return 'is-post'
}

/** 接口选中态的 Inspector：展示鉴权、参数、请求/响应形态、错误码与数据实现意向。 */
function EndpointInspector({ endpoint }: { endpoint: ProjectedEndpoint }): ReactElement {
  return (
    <div className={cx('planning-api-inspector')}>
      <div className={cx('planning-api-inspector-title')}>
        <span className={cx('planning-api-method', methodClass(endpoint.method))}>
          {endpoint.method}
        </span>
        <code>{endpoint.path}</code>
      </div>
      <dl className={cx('planning-api-inspector-grid')}>
        <div>
          <dt>鉴权</dt>
          <dd>
            {endpoint.operationType === 'custom' ? '按角色授权（见「权限」页签）' : '登录用户'}
          </dd>
        </div>
        <div>
          <dt>参数</dt>
          <dd>{endpoint.params}</dd>
        </div>
        <div>
          <dt>请求</dt>
          <dd>{endpoint.requestBody}</dd>
        </div>
        <div>
          <dt>响应</dt>
          <dd>
            <code>{endpoint.response}</code>
          </dd>
        </div>
        <div>
          <dt>错误码</dt>
          <dd>{endpoint.errors.join('、')}</dd>
        </div>
        <div>
          <dt>数据实现</dt>
          <dd>
            {endpoint.intentSources.length
              ? `意向 · ${endpoint.intentSources.join(' + ')}（开发阶段确认绑定与字段映射）`
              : '开发阶段选择数据来源后确认'}
          </dd>
        </div>
      </dl>
      {endpoint.outputs.length > 0 && (
        <div className={cx('planning-api-outputs')}>
          <Text type="secondary">响应字段</Text>
          <div>
            {endpoint.outputs.map((output) => (
              <Tag key={output}>{output}</Tag>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

/**
 * 技术规划方案「API 契约」页签：按实体逐个展示资源契约与接口列表；
 * 点击接口在对应资源卡内展开 Inspector。这里的交互只改变面板内的阅读位置，不产生确认动作。
 */
export function ApiContractsSection({
  contracts
}: {
  contracts: ProjectedContract[]
}): ReactElement {
  const allEndpoints = useMemo(
    () => contracts.flatMap((contract) => contract.endpoints),
    [contracts]
  )
  const [selectedEndpointId, setSelectedEndpointId] = useState(allEndpoints[0]?.id || '')
  const selectedEndpoint =
    allEndpoints.find((endpoint) => endpoint.id === selectedEndpointId) || allEndpoints[0]
  if (!contracts.length) {
    return <Empty description="需求说明书中尚未定义实体" image={Empty.PRESENTED_IMAGE_SIMPLE} />
  }
  return (
    <section>
      <div className={cx('planning-api-contract-list')}>
        {contracts.map((contract) => (
          <article className={cx('planning-api-contract')} key={contract.id}>
            <header>
              <strong>
                <ApiOutlined /> {contract.name}
              </strong>
              <code>{contract.basePath}</code>
              <Tag>{contract.endpoints.length} 个接口</Tag>
            </header>
            <div className={cx('planning-api-endpoint-table')}>
              <div className={cx('planning-api-endpoint-head')} aria-hidden="true">
                <span>方法</span>
                <span>路径</span>
                <span>说明</span>
                <span>页面</span>
              </div>
              {contract.endpoints.map((endpoint) => (
                <button
                  className={cx(
                    'planning-api-endpoint',
                    selectedEndpoint?.id === endpoint.id && 'is-selected'
                  )}
                  key={endpoint.id}
                  onClick={() => setSelectedEndpointId(endpoint.id)}
                  type="button"
                >
                  <span className={cx('planning-api-method', methodClass(endpoint.method))}>
                    {endpoint.method}
                  </span>
                  <code>{endpoint.path}</code>
                  <span>{endpoint.summary}</span>
                  <span className={cx('planning-api-endpoint-pages')}>
                    {endpoint.pages.length ? endpoint.pages.join('、') : '—'}
                  </span>
                </button>
              ))}
            </div>
            {selectedEndpoint &&
              contract.endpoints.some((endpoint) => endpoint.id === selectedEndpoint.id) && (
                <EndpointInspector endpoint={selectedEndpoint} />
              )}
          </article>
        ))}
      </div>
      <Text type="secondary">
        接口由实体操作投影生成；确认技术规划方案后，开发阶段在「实体」中逐操作确认数据实现与字段映射。
      </Text>
    </section>
  )
}
