import { DatabaseOutlined } from '@ant-design/icons'
import { Tag, Typography } from 'antd'
import type { ReactElement, ReactNode } from 'react'
import ProjectPlanPageTreePreview from '../ProjectPlanPageTreePreview'
import { projectPlanPageTreeNodes } from '../ProjectPlanPageTreePreview/pageTreeNodes'
import type { DevelopmentPlanningPageTreeNode } from '../../typings'
import { cx } from '../../utils'
import { PROJECT_PLAN_READING_SECTION_IDS } from './ProjectPlanReadingSections'
import './ProjectPlanSummary.less'

const { Paragraph, Text, Title } = Typography

type Props = {
  plan: Record<string, unknown>
}

type SectionProps = {
  anchorId?: string
  children: ReactNode
  count?: string
  description?: string
  title: string
}

const dataSourceTypeLabels: Record<string, string> = {
  database: '数据库',
  external_api: '外部 API',
  static: '静态数据'
}

type ProjectPlanEntity = {
  id: string
  name: string
  description: string
  dataSourceType: string
  fields: Array<{
    name: string
    label: string
    type: string
    required: boolean
    description: string
  }>
}

type ProjectPlanBusinessFlow = {
  id: string
  name: string
  steps: string[]
}

// 将未知值安全收窄为 ProjectPlan 子对象。
function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

// 仅保留 ProjectPlan 中有效的对象数组项。
function recordItems(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value)
    ? value.filter(
        (item): item is Record<string, unknown> =>
          Boolean(item) && typeof item === 'object' && !Array.isArray(item)
      )
    : []
}

// 将字符串或对象数组统一转换为可展示名称。
function itemLabels(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value
    .map((item) => {
      if (typeof item === 'string') return item.trim()
      const record = asRecord(item)
      return String(record.name || record.label || record.title || record.id || '').trim()
    })
    .filter(Boolean)
}

// 将任意结构化字段转换为稳定的单行文本。
function fieldText(value: unknown, fallback = ''): string {
  if (typeof value === 'string' || typeof value === 'number') return String(value).trim()
  return fallback
}

// 从兼容的 JSON/Python 字典字符串中读取指定文本字段。
function stringRecordField(value: string, key: string): string {
  const pattern = new RegExp(`(?:["']${key}["'])\\s*:\\s*(["'])(.*?)\\1`)
  return value.match(pattern)?.[2]?.trim() || ''
}

// 将实体字段对象收窄为可展示字段摘要。
function projectPlanEntityFields(value: unknown): ProjectPlanEntity['fields'] {
  if (!Array.isArray(value)) return []
  return value
    .map((item) => {
      if (!item || typeof item !== 'object' || Array.isArray(item)) return undefined
      const record = item as Record<string, unknown>
      const name = fieldText(record.name)
      if (!name) return undefined
      return {
        name,
        label: fieldText(record.label) || name,
        type: fieldText(record.type, 'text'),
        required: Boolean(record.required),
        description: fieldText(record.description)
      }
    })
    .filter((item): item is ProjectPlanEntity['fields'][number] => Boolean(item))
}

// 将实体对象或历史字符串化实体归一为名称和描述，避免把内部 schema_ref 当成实体文案。
function projectPlanEntities(value: unknown): ProjectPlanEntity[] {
  if (!Array.isArray(value)) return []
  return value
    .map((item) => {
      if (item && typeof item === 'object' && !Array.isArray(item)) {
        const record = item as Record<string, unknown>
        const rawDataSource = record.data_source
        return {
          id: fieldText(record.id) || fieldText(record.name),
          name: fieldText(record.name) || fieldText(record.id),
          description: fieldText(record.description),
          dataSourceType:
            typeof rawDataSource === 'string'
              ? rawDataSource
              : fieldText(asRecord(rawDataSource).type),
          fields: projectPlanEntityFields(record.fields)
        }
      }
      if (typeof item === 'string') {
        return {
          id: stringRecordField(item, 'id') || item.trim(),
          name: stringRecordField(item, 'name') || item.trim(),
          description: stringRecordField(item, 'description'),
          dataSourceType: '',
          fields: []
        }
      }
      return { id: '', name: '', description: '', dataSourceType: '', fields: [] }
    })
    .filter((item) => item.name || item.description || item.fields.length)
}

// 将业务流程统一归一为名称和步骤，供项目规划中的流程卡片展示。
function projectPlanBusinessFlows(value: unknown): ProjectPlanBusinessFlow[] {
  if (!Array.isArray(value)) return []
  return value
    .map((item) => {
      if (typeof item === 'string') {
        return { id: '', name: item.trim(), steps: [] }
      }
      const record = asRecord(item)
      const steps = Array.isArray(record.steps)
        ? record.steps.map((step) => fieldText(step)).filter(Boolean)
        : []
      return {
        id: fieldText(record.id),
        name: fieldText(record.name) || fieldText(record.id),
        steps
      }
    })
    .filter((flow) => flow.name || flow.steps.length)
}

// 将完整流程步骤压缩成设计稿中的一行主路径，保留未知流程的原始步骤作为兜底。
function projectPlanFlowSummary(flow: ProjectPlanBusinessFlow): string {
  const presetPaths: Record<string, string> = {
    browse_location_detail: '地点卡片 → 详情页',
    get_recommendation: '首页 → 偏好输入 → 推荐结果'
  }
  return presetPaths[flow.id] || flow.steps.join(' → ') || '流程步骤待补充'
}

// 将流程 ID 转为设计稿中的短标签，避免把内部下划线命名直接暴露给用户。
function projectPlanFlowCode(flow: ProjectPlanBusinessFlow): string {
  return (
    {
      browse_location_detail: 'DETAIL',
      get_recommendation: 'RECOMMEND',
      use_historical_preference: 'REUSE'
    }[flow.id] ||
    flow.id.replace(/_/g, ' ') ||
    'FLOW'
  ).toUpperCase()
}

// 把 ProjectPlan 状态代码转成用户可读文案。
function statusText(value: unknown): string {
  const status = fieldText(value, 'draft')
  return (
    {
      confirmed: '已确认',
      draft: '草稿',
      pending_user_confirmation: '待确认'
    }[status] || status
  )
}

// 递归统计页面树中的页面节点，用于概览区展示项目规模。
function pageTreeLeafCount(nodes: DevelopmentPlanningPageTreeNode[]): number {
  return nodes.reduce(
    (total, node) => total + (node.type === 'page' ? 1 : pageTreeLeafCount(node.children || [])),
    0
  )
}

// 渲染项目计划中的独立结构化模块。
function PlanSection({
  anchorId,
  children,
  count,
  description,
  title
}: SectionProps): ReactElement {
  return (
    <section className={cx('project-plan-summary-section')} id={anchorId}>
      <header>
        <div className={cx('project-plan-summary-section-heading')}>
          <Text strong>{title}</Text>
          {description ? <Text type="secondary">{description}</Text> : null}
        </div>
        {count ? <Text className={cx('project-plan-summary-section-index')}>{count}</Text> : null}
      </header>
      {children}
    </section>
  )
}

// 将数据源类型转换为稳定的界面展示文案。
function dataSourceTypeText(value: unknown): string {
  const type = fieldText(value).toLowerCase()
  return dataSourceTypeLabels[type] || (/[㐀-鿿]/.test(type) ? type : '其他数据源')
}

// 渲染单个实体卡片，展示实体字段与绑定的数据源类型。
function EntityItem({ entity, index }: { entity: ProjectPlanEntity; index: number }): ReactElement {
  return (
    <article className={cx('project-plan-summary-entity-card')}>
      <header className={cx('project-plan-summary-entity-card-header')}>
        <span className={cx('project-plan-summary-entity-card-icon')} aria-hidden="true">
          <DatabaseOutlined />
        </span>
        <div>
          <Text strong>{entity.name || `实体 ${index + 1}`}</Text>
          {entity.id ? <code>{entity.id}</code> : null}
          {entity.description ? (
            <Paragraph type="secondary">{entity.description}</Paragraph>
          ) : (
            <Text type="secondary">暂无实体描述</Text>
          )}
        </div>
        <Tag>
          {entity.dataSourceType ? dataSourceTypeText(entity.dataSourceType) : '待实体设计'}
        </Tag>
      </header>
      {entity.fields.length ? (
        <div className={cx('project-plan-summary-entity-fields')}>
          <div className={cx('project-plan-summary-entity-fields-head')}>
            <Text strong>名称</Text>
            <Text strong>字段</Text>
            <Text strong>说明</Text>
          </div>
          {entity.fields.map((field) => (
            <div className={cx('project-plan-summary-entity-field')} key={field.name}>
              <Text strong>{field.label || field.name}</Text>
              <code>{field.name}</code>
              <Text type="secondary">{field.description || '—'}</Text>
            </div>
          ))}
        </div>
      ) : (
        <Text type="secondary">暂无字段定义</Text>
      )}
    </article>
  )
}

// 从单个 API 契约中提取 Endpoint，并保留 method、path 与用途。
function ApiContractItem({
  contract,
  index
}: {
  contract: Record<string, unknown>
  index: number
}): ReactElement {
  const endpoints = recordItems(contract.endpoints)
  const resource = fieldText(contract.resource) || fieldText(contract.id) || `API ${index + 1}`
  const basePath = fieldText(contract.base_path)
  const authentication = asRecord(contract.authentication)
  const requiresAuthentication = Boolean(authentication.required)

  return (
    <article className={cx('project-plan-summary-api')}>
      <div className={cx('project-plan-summary-api-header')}>
        <div className={cx('project-plan-summary-item-title')}>
          <Text strong>{resource}</Text>
          {basePath ? <Tag className={cx('project-plan-code-tag')}>{basePath}</Tag> : null}
        </div>
        <Tag className={cx('project-plan-summary-api-authentication')}>
          {requiresAuthentication ? '需要认证' : '无需认证'}
        </Tag>
      </div>
      <div className={cx('project-plan-summary-api-list')}>
        {endpoints.length ? (
          endpoints.map((endpoint, endpointIndex) => {
            const method = fieldText(endpoint.method, 'GET').toUpperCase()
            const path = fieldText(endpoint.path)
            return (
              <div
                className={cx('project-plan-summary-endpoint')}
                key={fieldText(endpoint.id) || `${resource}-${endpointIndex}`}
              >
                <Tag>{method}</Tag>
                <Tag className={cx('project-plan-code-tag')}>{path || basePath || '/'}</Tag>
                <Text>{fieldText(endpoint.summary, '待补充接口说明')}</Text>
              </div>
            )
          })
        ) : (
          <Text type="secondary">暂无 Endpoint</Text>
        )}
      </div>
    </article>
  )
}

// 仅以 ProjectPlan JSON 中的正式字段构建项目规划确认界面。
export default function ProjectPlanSummary({ plan }: Props): ReactElement {
  const app = asRecord(plan.app)
  const overview = asRecord(plan.requirements_overview)
  const architecture = asRecord(plan.architecture)
  const backendStack = asRecord(architecture.backend_tech_stack)
  const acceptanceCriteria = itemLabels(
    plan.project_acceptance_criteria || plan.acceptance_criteria
  )
  const roles = itemLabels(overview.roles)
  const modules = itemLabels(overview.modules)
  const businessFlowDetails = projectPlanBusinessFlows(
    overview.business_flows || plan.business_flows
  )
  const businessFlows = businessFlowDetails.map((flow) => flow.name)
  const apiContracts = recordItems(plan.api_contracts)
  const pageTree = projectPlanPageTreeNodes(
    plan.artifact_type === 'technical-plan' ? plan.pages : plan.frontend_pages
  )
  const entities = projectPlanEntities(plan.entities)
  const dataSources = recordItems(plan.data_sources)
  const datasourceType = entities[0]?.dataSourceType || fieldText(dataSources[0]?.type, 'database')
  const isStaticDatasource = datasourceType === 'static'
  const permissionModel = asRecord(plan.permission_model)
  const permissionRoles = recordItems(permissionModel.roles)
  const operationPermissions = recordItems(permissionModel.operation_permissions)
  const appName = fieldText(app.name, '未命名应用')
  const planStatus = statusText(plan.confirmation_status || plan.status)
  const planVersion = fieldText(plan.version, '0.1.0')
  const appSubtitle = fieldText(overview.subtitle, `PC 端 ${appName}应用`)
  const appGoal = fieldText(
    overview.app_goal,
    '通过清晰的页面、接口和数据关系完成可落地的应用工程规划。'
  )
  const pageCount = pageTreeLeafCount(pageTree)

  return (
    <div className={cx('project-plan-summary')}>
      <section className={cx('project-plan-summary-hero')}>
        <div className={cx('project-plan-summary-hero-copy')}>
          <Text className={cx('project-plan-summary-eyebrow')}>PROJECT PLAN / {planVersion}</Text>
          <Title level={3}>{appName}</Title>
          <Paragraph type="secondary">{appSubtitle}</Paragraph>
        </div>
        <div className={cx('project-plan-summary-hero-meta')}>
          <Tag className={cx('project-plan-summary-status-tag')}>{planStatus}</Tag>
          <Text type="secondary">版本 {planVersion}</Text>
        </div>
      </section>

      <div className={cx('project-plan-summary-metrics')} aria-label="项目规模概览">
        <div>
          <Text strong>{modules.length}</Text>
          <Text type="secondary">功能模块</Text>
        </div>
        <div>
          <Text strong>{pageCount}</Text>
          <Text type="secondary">应用页面</Text>
        </div>
        <div>
          <Text strong>{businessFlows.length}</Text>
          <Text type="secondary">核心流程</Text>
        </div>
        <div>
          <Text strong>{entities.length}</Text>
          <Text type="secondary">实体</Text>
        </div>
      </div>

      <div className={cx('project-plan-summary-grid')}>
        <PlanSection
          anchorId={PROJECT_PLAN_READING_SECTION_IDS.overview}
          count="01"
          description="目标、角色和功能范围"
          title="项目概述"
        >
          <div className={cx('project-plan-summary-overview')}>
            <dl className={cx('project-plan-summary-facts')}>
              <div>
                <dt>目标</dt>
                <dd>{fieldText(overview.target, appGoal)}</dd>
              </div>
              <div>
                <dt>角色</dt>
                <dd>{roles.join('、') || '待补充'}</dd>
              </div>
              <div>
                <dt>终端</dt>
                <dd>PC · 侧边导航 · 无需登录</dd>
              </div>
            </dl>
            <div className={cx('project-plan-summary-overview-modules')}>
              <Text type="secondary">功能模块</Text>
              {modules.length ? (
                <div className={cx('project-plan-summary-tags')}>
                  {modules.map((item) => (
                    <Tag key={item}>{item}</Tag>
                  ))}
                </div>
              ) : (
                <Text type="secondary">暂无功能模块</Text>
              )}
            </div>
          </div>
        </PlanSection>

        <PlanSection
          anchorId={PROJECT_PLAN_READING_SECTION_IDS.architecture}
          count="4 个层次"
          description="实现边界和运行依赖"
          title="技术底座"
        >
          <dl className={cx('project-plan-summary-facts', 'is-architecture')}>
            <div>
              <dt>前端</dt>
              <dd>{fieldText(architecture.frontend, '待补充')}</dd>
            </div>
            <div>
              <dt>后端</dt>
              <dd>{fieldText(architecture.backend, '待补充')}</dd>
            </div>
            <div>
              <dt>数据</dt>
              <dd>{fieldText(architecture.data, '待补充')}</dd>
            </div>
            <div>
              <dt>测试</dt>
              <dd>{fieldText(architecture.testing, '待补充')}</dd>
            </div>
          </dl>
          {Object.keys(backendStack).length ? (
            <div className={cx('project-plan-summary-tags')}>
              {Object.entries(backendStack).map(([key, value]) => (
                <Tag key={key}>{fieldText(value, key)}</Tag>
              ))}
            </div>
          ) : null}
        </PlanSection>

        {businessFlowDetails.length ? (
          <PlanSection
            anchorId={PROJECT_PLAN_READING_SECTION_IDS.experience}
            count={`${businessFlowDetails.length} 条流程`}
            description="从输入偏好到查看详情"
            title="核心流程"
          >
            <div className={cx('project-plan-summary-flow-list')}>
              {businessFlowDetails.map((flow, flowIndex) => (
                <article className={cx('project-plan-summary-flow')} key={flow.name || flowIndex}>
                  <Text className={cx('project-plan-summary-flow-index')}>
                    {String(flowIndex + 1).padStart(2, '0')} / {projectPlanFlowCode(flow)}
                  </Text>
                  <Text strong>{flow.name || `流程 ${flowIndex + 1}`}</Text>
                  <Paragraph type="secondary">{projectPlanFlowSummary(flow)}</Paragraph>
                </article>
              ))}
            </div>
          </PlanSection>
        ) : null}

        {pageTree.length ? (
          <PlanSection
            anchorId={
              businessFlowDetails.length ? undefined : PROJECT_PLAN_READING_SECTION_IDS.experience
            }
            count={`${pageCount} 个页面`}
            description="菜单和页面归属关系"
            title="页面地图"
          >
            <ProjectPlanPageTreePreview nodes={pageTree} title="菜单与页面" />
          </PlanSection>
        ) : null}

        {apiContracts.length ? (
          <PlanSection
            anchorId={PROJECT_PLAN_READING_SECTION_IDS.data}
            count={`${apiContracts.length} 个资源`}
            description={
              isStaticDatasource
                ? '供页面设计与前端数据访问模块使用，不代表真实 HTTP 后端'
                : '真实后端资源和 HTTP 接口路径'
            }
            title={isStaticDatasource ? '前端 Mock 数据契约' : '真实 HTTP API 契约'}
          >
            <div className={cx('project-plan-summary-api-grid')}>
              {apiContracts.map((contract, index) => (
                <ApiContractItem
                  contract={contract}
                  index={index}
                  key={fieldText(contract.id) || `api-${index}`}
                />
              ))}
            </div>
          </PlanSection>
        ) : null}

        {entities.length ? (
          <PlanSection
            anchorId={apiContracts.length ? undefined : PROJECT_PLAN_READING_SECTION_IDS.data}
            count={`${entities.length} 个实体`}
            description="业务实体及其字段定义，标注数据来源类型"
            title="实体"
          >
            <div className={cx('project-plan-summary-entity-grid')}>
              {entities.map((entity, index) => (
                <EntityItem entity={entity} index={index} key={entity.name || `entity-${index}`} />
              ))}
            </div>
          </PlanSection>
        ) : null}

        <PlanSection
          anchorId={PROJECT_PLAN_READING_SECTION_IDS.acceptance}
          count="待检查"
          description="谁可以使用，以及如何判断完成"
          title="权限与验收"
        >
          <div className={cx('project-plan-summary-permission-acceptance')}>
            <div className={cx('project-plan-summary-permission')}>
              <Text type="secondary">角色与操作</Text>
              {permissionRoles.length ? (
                permissionRoles.map((role, roleIndex) => (
                  <div
                    className={cx('project-plan-summary-permission-role')}
                    key={fieldText(role.id) || fieldText(role.name) || `role-${roleIndex}`}
                  >
                    <Text strong>{fieldText(role.name, fieldText(role.id, '旅行者'))}</Text>
                    {fieldText(role.description) ? (
                      <Text type="secondary">{fieldText(role.description)}</Text>
                    ) : null}
                  </div>
                ))
              ) : (
                <Text type="secondary">暂无角色权限说明</Text>
              )}
              <div className={cx('project-plan-summary-permission-tags')}>
                {operationPermissions.map((operation, operationIndex) => (
                  <Tag key={fieldText(operation.operation) || `operation-${operationIndex}`}>
                    {fieldText(operation.description, fieldText(operation.operation))}
                  </Tag>
                ))}
              </div>
            </div>
            <div className={cx('project-plan-summary-acceptance')}>
              <Text type="secondary">验收清单</Text>
              {acceptanceCriteria.length ? (
                <ul className={cx('project-plan-summary-list')}>
                  {acceptanceCriteria.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              ) : (
                <Text type="secondary">暂无验收标准</Text>
              )}
            </div>
          </div>
        </PlanSection>
      </div>
    </div>
  )
}
