import { ApiOutlined, ClusterOutlined, DatabaseOutlined } from '@ant-design/icons'
import { Tabs, Typography } from 'antd'
import type { ReactElement } from 'react'
import { useMemo } from 'react'
import TechnicalBusinessObjectsReview from '../../../BusinessObjects/TechnicalBusinessObjectsReview'
import { useBusinessObjects } from '../../../BusinessObjects/store'
import {
  DATABASE_MODE_LABEL,
  externalOperationCount,
  useDataSourceIndex
} from '../../../DataSources/catalog'
import { cx } from '../../../../utils'
import { AuthorizationSection } from './TechnicalPlanAuthorization'
import { ApiContractsSection } from './TechnicalPlanContracts'
import { apiContractsFromObjects } from './TechnicalPlanContractsProjection'
import './TechnicalPlanReview.less'

const { Paragraph, Text, Title } = Typography

/** 把未知对象安全收窄为记录，避免演示数据缺字段导致整个审阅面板白屏。 */
function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

/** 读取对象中的第一个非空展示字段。 */
function textOf(value: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) {
    const candidate = value[key]
    if (typeof candidate === 'string' && candidate.trim()) return candidate.trim()
  }
  return ''
}

/** 「架构」页签：三层技术架构 + 数据架构（接入来源目录）。 */
function ArchitectureContent({ value }: { value: Record<string, unknown> }): ReactElement {
  const architecture = asRecord(value.architecture)
  const dataSourceIndex = useDataSourceIndex()
  return (
    <section>
      <Title level={5}>
        <ClusterOutlined /> 技术架构
      </Title>
      <div className={cx('planning-architecture-grid')}>
        {['frontend', 'backend', 'data'].map((key) => (
          <article key={key}>
            <Text type="secondary">
              {key === 'frontend' ? '前端' : key === 'backend' ? '后端' : '数据'}
            </Text>
            <Paragraph>{String(architecture[key] || '待补充')}</Paragraph>
          </article>
        ))}
      </div>
      <Title level={5}>
        <DatabaseOutlined /> 数据架构 · 接入来源
      </Title>
      <div className={cx('planning-data-architecture')}>
        {dataSourceIndex.databases.map((source) => (
          <article key={source.id}>
            <strong>
              <DatabaseOutlined /> {source.name}
            </strong>
            <small>
              {DATABASE_MODE_LABEL[source.mode]} · {source.tables.length} 张表
            </small>
            <span>{source.tables.map((table) => table.name).join('、') || '尚未登记数据表'}</span>
          </article>
        ))}
        {dataSourceIndex.externalServices.map((source) => (
          <article key={source.id}>
            <strong>
              <ApiOutlined /> {source.name}
            </strong>
            <small>
              {externalOperationCount(source)} 个接口 · {source.directories.length} 个目录
            </small>
            <span>
              {source.directories
                .flatMap((directory) => directory.operations.map((operation) => operation.name))
                .join('、') || '尚未登记接口'}
            </span>
          </article>
        ))}
        {!dataSourceIndex.sources.length && (
          <Text type="secondary">
            尚未登记数据来源；可在左侧「数据来源」中接入数据库与外部服务。
          </Text>
        )}
      </div>
      <Text type="secondary">
        来源目录在左侧「数据来源」中维护；每个实体操作在开发阶段选择具体数据能力并完成字段映射。
      </Text>
    </section>
  )
}

/** 「页面」页签里的单个页面卡：页面 → 调用的实体操作 → 承接的接口。 */
function PageBindingCard({
  page,
  requirementPages,
  objects,
  endpointByOperation
}: {
  page: Record<string, unknown>
  requirementPages: Record<string, unknown>[]
  objects: ReturnType<typeof useBusinessObjects>[0]
  endpointByOperation: Map<string, { method: string; path: string }>
}): ReactElement {
  const pageId = textOf(page, 'pageId', 'id')
  const requirementPage = requirementPages.find((item) => textOf(item, 'pageId', 'id') === pageId)
  const pageName = textOf(requirementPage || page, 'name', 'pageId', 'id')
  const usages = objects.flatMap((object) =>
    object.operations
      .filter((operation) => operation.pages.includes(pageName))
      .map((operation) => ({
        call: `${object.name}.${operation.name}()`,
        purpose: operation.description,
        endpoint: endpointByOperation.get(`${object.id}.${operation.id}`)
      }))
  )
  return (
    <article>
      <div className={cx('planning-page-binding-heading')}>
        <Text strong>{pageName}</Text>
        {textOf(requirementPage || page, 'path') ? (
          <code>{textOf(requirementPage || page, 'path')}</code>
        ) : null}
      </div>
      <Paragraph>
        {textOf(requirementPage || page, 'description') || '页面通过实体操作使用数据能力。'}
      </Paragraph>
      <div className={cx('planning-page-usages')}>
        {usages.length ? (
          usages.map((usage) => (
            <div className={cx('planning-page-usage')} key={usage.call}>
              <strong>{usage.call}</strong>
              {usage.endpoint ? (
                <code>
                  {usage.endpoint.method} {usage.endpoint.path}
                </code>
              ) : (
                <Text type="secondary">开发阶段落实接口</Text>
              )}
            </div>
          ))
        ) : (
          <div className={cx('planning-page-usage')}>
            <strong>静态页面 · 无需数据操作</strong>
          </div>
        )}
      </div>
      <Text type="secondary">页面只调用实体操作；数据库与外部服务由操作的数据实现承接。</Text>
    </article>
  )
}

/**
 * 计划阶段技术规划方案审阅：架构 / 实体 / API 契约 / 页面 / 权限 五个二级页签。
 * 右侧只承载静态审阅与阅读位置切换；确认动作仍在对话区的「确认技术规划方案」卡完成。
 */
export default function TechnicalPlanReview({
  value,
  requirementSpec
}: {
  value: Record<string, unknown>
  requirementSpec: Record<string, unknown>
}): ReactElement {
  const technicalPages = Array.isArray(value.pages)
    ? value.pages.map(asRecord).filter((item) => Object.keys(item).length)
    : []
  const requirementPages = Array.isArray(requirementSpec.pages)
    ? requirementSpec.pages.map(asRecord).filter((item) => Object.keys(item).length)
    : []
  const pages = technicalPages.length > 0 ? technicalPages : requirementPages
  const [objects] = useBusinessObjects(requirementSpec)
  const contracts = useMemo(() => apiContractsFromObjects(objects), [objects])
  const endpointByOperation = useMemo(
    () =>
      new Map(
        contracts.flatMap((contract) =>
          contract.endpoints.map((endpoint) => [
            endpoint.id,
            { method: endpoint.method, path: endpoint.path }
          ])
        )
      ),
    [contracts]
  )
  return (
    <div className={cx('planning-review-document')}>
      <Tabs
        defaultActiveKey="business-objects"
        items={[
          { key: 'architecture', label: '架构', children: <ArchitectureContent value={value} /> },
          {
            key: 'business-objects',
            label: '实体',
            children: <TechnicalBusinessObjectsReview requirementSpec={requirementSpec} />
          },
          {
            key: 'api-contracts',
            label: 'API 契约',
            children: <ApiContractsSection contracts={contracts} />
          },
          {
            key: 'pages',
            label: '页面',
            children: (
              <section>
                <Title level={5}>页面绑定的业务能力</Title>
                <div className={cx('planning-review-collection')}>
                  {pages.map((page, index) => (
                    <PageBindingCard
                      endpointByOperation={endpointByOperation}
                      key={textOf(page, 'pageId', 'id') || index}
                      objects={objects}
                      page={page}
                      requirementPages={requirementPages}
                    />
                  ))}
                </div>
              </section>
            )
          },
          {
            key: 'authorization',
            label: '权限',
            children: (
              <AuthorizationSection contracts={contracts} requirementSpec={requirementSpec} />
            )
          }
        ]}
      />
    </div>
  )
}
