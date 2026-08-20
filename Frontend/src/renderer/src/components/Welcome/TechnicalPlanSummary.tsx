import { Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../utils'
import { PROJECT_PLAN_READING_SECTION_IDS } from './ProjectPlanReadingSections'
import './ProjectPlanSummary.less'

const { Paragraph, Text, Title } = Typography

type Props = {
  plan: Record<string, unknown>
}

/** 把未知值收窄为普通 JSON 对象。 */
function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

/** 只保留数组中的 JSON 对象。 */
function recordItems(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? value.map(asRecord).filter((item) => Object.keys(item).length) : []
}

/** 把技术规划确认状态转换为开发可读文案。 */
function statusText(value: unknown): string {
  if (value === 'confirmed') return '已确认'
  if (value === 'pending_user_confirmation') return '待确认'
  return String(value || '草稿')
}

/** 渲染 TechnicalPlan 的开发审核视图，不投射上游产品事实。 */
export default function TechnicalPlanSummary({ plan }: Props): ReactElement {
  const architecture = asRecord(plan.architecture)
  const entities = recordItems(plan.entities)
  const contracts = recordItems(plan.api_contracts)
  const pages = recordItems(plan.pages)

  return (
    <div className={cx('project-plan-summary')}>
      <section className={cx('project-plan-summary-hero')}>
        <div className={cx('project-plan-summary-hero-copy')}>
          <Text className={cx('project-plan-summary-eyebrow')}>TECHNICAL PLAN</Text>
          <Title level={3}>开发技术规划</Title>
          <Paragraph type="secondary">仅展示本阶段新增的实现决策</Paragraph>
        </div>
        <div className={cx('project-plan-summary-hero-meta')}>
          <Tag className={cx('project-plan-summary-status-tag')}>
            {statusText(plan.confirmation_status)}
          </Tag>
          <Text type="secondary">{String(plan.artifact_type || 'technical-plan')}</Text>
        </div>
      </section>

      <div className={cx('project-plan-summary-metrics')} aria-label="技术规划规模">
        <div><Text strong>{contracts.length}</Text><Text type="secondary">API 契约</Text></div>
        <div><Text strong>{entities.length}</Text><Text type="secondary">业务实体</Text></div>
        <div><Text strong>{pages.length}</Text><Text type="secondary">页面引用</Text></div>
      </div>

      <div className={cx('project-plan-summary-grid')}>
        <section
          className={cx('project-plan-summary-section')}
          id={PROJECT_PLAN_READING_SECTION_IDS.architecture}
        >
          <div className={cx('project-plan-summary-section-heading')}>
            <div><Title level={4}>技术架构</Title><Text type="secondary">实现边界与固定技术栈</Text></div>
          </div>
          <dl className={cx('project-plan-summary-facts', 'is-architecture')}>
            <div><dt>前端</dt><dd>{String(architecture.frontend || '待补充')}</dd></div>
            <div><dt>后端</dt><dd>{String(architecture.backend || '待补充')}</dd></div>
            <div><dt>数据</dt><dd>{String(architecture.data || '待补充')}</dd></div>
          </dl>
        </section>

        <section
          className={cx('project-plan-summary-section')}
          id={PROJECT_PLAN_READING_SECTION_IDS.data}
        >
          <div className={cx('project-plan-summary-section-heading')}>
            <div><Title level={4}>业务实体</Title><Text type="secondary">API Contract 与 EntityDesign 的唯一字段事实源</Text></div>
          </div>
          <div className={cx('project-plan-summary-entity-grid')}>
            {entities.map((entity, index) => (
              <article className={cx('project-plan-summary-entity-card')} key={String(entity.id || index)}>
                <header className={cx('project-plan-summary-entity-card-header')}>
                  <div>
                    <Text strong>{String(entity.name || `实体 ${index + 1}`)}</Text>
                    <code>{String(entity.id || `entity-${index + 1}`)}</code>
                    <Paragraph type="secondary">{String(entity.description || '暂无实体描述')}</Paragraph>
                  </div>
                  <Tag>{recordItems(entity.fields).length} 个字段</Tag>
                </header>
                <div className={cx('project-plan-summary-entity-fields')}>
                  {recordItems(entity.fields).map((field, fieldIndex) => (
                    <div key={String(field.name || fieldIndex)}>
                      <Text>{String(field.label || field.name || `字段 ${fieldIndex + 1}`)}</Text>
                      <code>{String(field.name || '')}</code>
                      <Tag>{String(field.type || 'text')}</Tag>
                    </div>
                  ))}
                </div>
              </article>
            ))}
          </div>
        </section>

        <section
          className={cx('project-plan-summary-section')}
        >
          <div className={cx('project-plan-summary-section-heading')}>
            <div><Title level={4}>API 契约</Title><Text type="secondary">Schema 与 Endpoint 的唯一技术定义</Text></div>
          </div>
          <div className={cx('project-plan-summary-api-grid')}>
            {contracts.map((contract, index) => (
              <article className={cx('project-plan-summary-api')} key={String(contract.id || index)}>
                <Text strong>{String(contract.id || `contract-${index + 1}`)}</Text>
                <div className={cx('project-plan-summary-api-list')}>
                  {recordItems(contract.endpoints).map((endpoint, endpointIndex) => (
                    <div className={cx('project-plan-summary-endpoint')} key={String(endpoint.id || endpointIndex)}>
                      <Tag>{String(endpoint.method || 'GET')}</Tag>
                      <Text>{String(endpoint.path || contract.base_path || '/')}</Text>
                      <Text type="secondary">{String(endpoint.summary || '')}</Text>
                    </div>
                  ))}
                </div>
              </article>
            ))}
          </div>
        </section>

        <section
          className={cx('project-plan-summary-section')}
          id={PROJECT_PLAN_READING_SECTION_IDS.experience}
        >
          <div className={cx('project-plan-summary-section-heading')}>
            <div><Title level={4}>页面技术引用</Title><Text type="secondary">只保存页面对 Endpoint 的新增依赖和实现选择</Text></div>
          </div>
          <div className={cx('project-plan-summary-items-grid')}>
            {pages.map((page, index) => {
              const references = asRecord(page.references)
              return (
                <article className={cx('project-plan-summary-api')} key={String(page.pageId || index)}>
                  <Text strong>{String(page.pageId || `page-${index + 1}`)}</Text>
                  <Text type="secondary">
                    {recordItems(references.endpoint_dependencies).length} 个 Endpoint · {recordItems(references.action_implementations).length} 个 Action 实现
                  </Text>
                </article>
              )
            })}
          </div>
        </section>
      </div>
    </div>
  )
}
