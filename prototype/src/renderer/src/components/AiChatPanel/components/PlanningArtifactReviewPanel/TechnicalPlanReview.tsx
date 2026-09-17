import { ClusterOutlined, DatabaseOutlined } from '@ant-design/icons'
import { Tabs, Typography } from 'antd'
import type { ReactElement } from 'react'
import { useMemo } from 'react'
import { useAppApis } from '../../../AppApis/store'
import {
  DATABASE_MODE_LABEL,
  externalApiSignature,
  useDataSourceIndex
} from '../../../DataSources/catalog'
import { cx } from '../../../../utils'
import { AuthorizationSection } from './TechnicalPlanAuthorization'
import { AppApiContractsSection } from './AppApiContracts'
import { apiEndpointsFromObjects, type ProjectedEndpoint } from './AppApiContractsProjection'
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

/** 「架构」页签：技术架构三段说明 + 数据架构接入来源，平铺的段落与清单排版。 */
function ArchitectureContent({ value }: { value: Record<string, unknown> }): ReactElement {
  const architecture = asRecord(value.architecture)
  const dataSourceIndex = useDataSourceIndex()
  return (
    <section>
      <Title level={5}>
        <ClusterOutlined /> 技术架构
      </Title>
      {(['frontend', 'backend', 'data'] as const).map((key) => (
        <Paragraph key={key}>
          <Text strong>{key === 'frontend' ? '前端：' : key === 'backend' ? '后端：' : '数据：'}</Text>
          {String(architecture[key] || '待补充')}
        </Paragraph>
      ))}
      <Title level={5}>
        <DatabaseOutlined /> 数据架构 · 接入来源
      </Title>
      <ul>
        {dataSourceIndex.databases.map((source) => {
          const imported = source.tables.filter((table) => table.imported)
          return (
            <li key={source.id}>
              <Text strong>{source.name}</Text>：{DATABASE_MODE_LABEL[source.mode]} · 已引入 {imported.length}{' '}
              张表（{imported.map((table) => table.name).join('、') || '尚未引入数据表'}）
            </li>
          )
        })}
        {dataSourceIndex.externalServices.map((source) => (
          <li key={source.id}>
            <Text strong>{source.name}</Text>：外部 API ·{' '}
            <Text code>{externalApiSignature(source)}</Text>（{source.description}）
          </li>
        ))}
      </ul>
      {!dataSourceIndex.sources.length && (
        <Paragraph type="secondary">
          尚未登记数据来源；可在左侧「数据来源」中接入数据库与外部服务。
        </Paragraph>
      )}
    </section>
  )
}

/** 「应用页面」页签里的单个页面卡：页面标题行 + 描述 + 调用的应用API清单（平铺文档行）。 */
function PageBindingCard({
  page,
  requirementPages,
  objects,
  endpointByApi
}: {
  page: Record<string, unknown>
  requirementPages: Record<string, unknown>[]
  objects: ReturnType<typeof useAppApis>[0]
  endpointByApi: Map<string, ProjectedEndpoint>
}): ReactElement {
  const pageId = textOf(page, 'pageId', 'id')
  const requirementPage = requirementPages.find((item) => textOf(item, 'pageId', 'id') === pageId)
  const pageName = textOf(requirementPage || page, 'name', 'pageId', 'id')
  const usages = objects
    .filter((object) => object.pages.includes(pageName))
    .map((object) => ({
      call: `${object.name}()`,
      endpoint: endpointByApi.get(object.id)
    }))
  return (
    <article>
      <div className={cx('planning-page-binding-heading')}>
        <Text strong>{pageName}</Text>
        {textOf(requirementPage || page, 'path') ? (
          <code>{textOf(requirementPage || page, 'path')}</code>
        ) : null}
      </div>
      <Paragraph>
        {textOf(requirementPage || page, 'description') || '应用页面通过应用API使用数据能力。'}
      </Paragraph>
      {usages.length ? (
        <ul>
          {usages.map((usage) => (
            <li key={usage.call}>
              <Text strong>{usage.call}</Text>{' '}
              {usage.endpoint ? (
                <Text code>
                  {usage.endpoint.method} {usage.endpoint.path}
                </Text>
              ) : (
                <Text type="secondary">接口在开发阶段落实</Text>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <Paragraph type="secondary">静态应用页面，无需数据操作</Paragraph>
      )}
    </article>
  )
}

/**
 * 计划阶段技术规划方案审阅：架构 / 应用API / 页面 / 权限 四个二级页签。
 * 全部页签采用与需求文档一致的平铺文档排版（段落、清单、collection 卡片，无强调底色）；
 * 应用API页签是静态接口文档：需求契约只声明接口面，这里为每个接口铺开技术细节与数据实现意向。
 * 右侧只承载静态审阅；确认动作仍在对话区的「确认技术规划方案」卡完成。
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
  const [objects] = useAppApis(requirementSpec, undefined, value)
  const endpoints = useMemo(() => apiEndpointsFromObjects(objects), [objects])
  const endpointByApi = useMemo(
    () => new Map<string, ProjectedEndpoint>(endpoints.map((endpoint) => [endpoint.id, endpoint])),
    [endpoints]
  )
  return (
    <div className={cx('planning-review-document')}>
      <Tabs
        defaultActiveKey="app-apis"
        items={[
          { key: 'architecture', label: '架构', children: <ArchitectureContent value={value} /> },
          {
            key: 'app-apis',
            label: '应用API',
            children: <AppApiContractsSection endpoints={endpoints} />
          },
          {
            key: 'pages',
            label: '应用页面',
            children: (
              <section>
                <Title level={5}>应用页面绑定的业务能力</Title>
                <div className={cx('planning-review-collection')}>
                  {pages.map((page, index) => (
                    <PageBindingCard
                      endpointByApi={endpointByApi}
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
              <AuthorizationSection endpoints={endpoints} requirementSpec={requirementSpec} />
            )
          }
        ]}
      />
    </div>
  )
}
