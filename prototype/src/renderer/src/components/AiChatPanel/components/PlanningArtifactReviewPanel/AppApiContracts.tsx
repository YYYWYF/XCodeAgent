import { Empty, Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import type { ProjectedEndpoint } from './AppApiContractsProjection'

const { Paragraph, Text, Title } = Typography

/** 单个接口的静态文档节：标题行、用途、鉴权/参数/请求/响应/错误码、数据实现与响应字段全部直接铺开。 */
function ApiContractArticle({ endpoint }: { endpoint: ProjectedEndpoint }): ReactElement {
  return (
    <article>
      <div className={cx('planning-review-item-title')}>
        <Text strong>{endpoint.operationName}</Text>
        <Text code>
          {endpoint.method} {endpoint.path}
        </Text>
        <span className={cx('planning-api-contract-pages')}>
          {endpoint.pages.length ? `调用页面：${endpoint.pages.join('、')}` : '调用页面：待补充'}
        </span>
      </div>
      <Paragraph>{endpoint.summary}</Paragraph>
      <dl className={cx('planning-api-detail-grid')}>
        <div>
          <dt>鉴权</dt>
          <dd>按角色授权</dd>
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
      </dl>
      <Title level={5}>数据实现</Title>
      {endpoint.intentSources.length ? (
        <Paragraph>意向 · {endpoint.intentSources.join(' + ')}</Paragraph>
      ) : (
        <Paragraph type="secondary">开发阶段确认数据来源</Paragraph>
      )}
      {endpoint.outputs.length > 0 && (
        <>
          <Title level={5}>响应字段</Title>
          <div className={cx('planning-api-output-fields')}>
            {endpoint.outputs.map((output) => (
              <Tag key={output}>{output}</Tag>
            ))}
          </div>
        </>
      )}
    </article>
  )
}

/**
 * 技术规划方案「应用API」页签：静态接口文档，排版与需求文档各分栏同一套（标题行 + 段落 + h5 分节）。
 * 沿用原工程 TechnicalPlan 的开发侧定位——需求文档的 API 契约只声明接口面，技术细节（参数、
 * 响应 Schema、错误码）与数据实现意向由计划阶段为每个接口补全；本页签不做任何选择交互。
 */
export function AppApiContractsSection({
  endpoints
}: {
  endpoints: ProjectedEndpoint[]
}): ReactElement {
  if (!endpoints.length) {
    return <Empty description="需求说明书中尚未定义API契约" image={Empty.PRESENTED_IMAGE_SIMPLE} />
  }
  return (
    <section>
      <div className={cx('planning-review-collection')}>
        {endpoints.map((endpoint) => (
          <ApiContractArticle endpoint={endpoint} key={endpoint.id} />
        ))}
      </div>
    </section>
  )
}
