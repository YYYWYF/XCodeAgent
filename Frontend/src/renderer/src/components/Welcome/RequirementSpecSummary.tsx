import {
  DatabaseOutlined,
  DesktopOutlined,
  FileTextOutlined,
  FlagOutlined,
  PartitionOutlined,
  TeamOutlined
} from '@ant-design/icons'
import { Tag, Typography } from 'antd'
import type { ReactElement, ReactNode } from 'react'
import { cx } from '../../utils'
import './RequirementSpecSummary.less'

const { Paragraph, Text, Title } = Typography

type Props = {
  spec: Record<string, unknown>
}

// 将未知值安全收窄为可读取的对象。
function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined
}

// 将未知集合收窄为数组，避免模型缺省字段影响概览渲染。
function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : []
}

// 读取对象中的首个有效文本字段，并兼容历史 Python 字典字符串。
function itemText(value: unknown, keys: string[]): string {
  const record = asRecord(value)
  if (record) {
    for (const key of keys) {
      const text = record[key]
      if (typeof text === 'string' && text.trim()) return text.trim()
    }
  }
  if (typeof value !== 'string') return ''
  for (const key of keys) {
    const match = value.match(new RegExp(`["']${key}["']\\s*:\\s*["']([^"']+)["']`))
    if (match?.[1]) return match[1].trim()
  }
  return value.trim()
}

// 将字符串数组或对象名称统一为可展示标签。
function itemLabels(value: unknown): string[] {
  return asArray(value)
    .map((item) => typeof item === 'string' ? item : itemText(item, ['name', 'label']))
    .filter(Boolean)
}

type EntityField = {
  label: string
  description: string
}

type SourceEntity = {
  id: string
  name: string
  description: string
  fields: EntityField[]
}

// 将实体字段对象安全收窄为需求层的展示信息（名称与说明，不含字段名和类型）。
function entityFields(value: unknown): EntityField[] {
  return asArray(value)
    .map((item) => {
      const record = asRecord(item)
      if (!record) return undefined
      const label = itemText(record, ['label', 'name'])
      if (!label) return undefined
      return {
        label,
        description: itemText(record, ['description'])
      }
    })
    .filter((item): item is EntityField => Boolean(item))
}

// 将数据源实体（对象或旧字符串）归一为带字段摘要的展示结构。
function sourceEntities(value: unknown): SourceEntity[] {
  return asArray(value)
    .map((item, index) => {
      const record = asRecord(item)
      if (record) {
        return {
          id: itemText(record, ['id', 'name']) || `Entity${index + 1}`,
          name: itemText(record, ['name', 'id']) || `Entity${index + 1}`,
          description: itemText(record, ['description']),
          fields: entityFields(record.fields)
        }
      }
      if (typeof item === 'string' && item.trim()) {
        return {
          id: item.trim(),
          name: item.trim(),
          description: '',
          fields: []
        }
      }
      return undefined
    })
    .filter((item): item is SourceEntity => Boolean(item))
}

// 渲染带图标和标题的需求概览分区。
function SummarySection({
  children,
  icon,
  title
}: {
  children: ReactNode
  icon: ReactNode
  title: string
}): ReactElement {
  return (
    <section className={cx('requirement-summary-section')}>
      <header>
        <span className={cx('requirement-summary-section-icon')}>{icon}</span>
        <Title level={5}>{title}</Title>
      </header>
      {children}
    </section>
  )
}

// 渲染名称、说明和可选标签组成的紧凑信息卡。
function SummaryItem({
  description,
  labels,
  name,
  route
}: {
  description?: string
  labels?: string[]
  name: string
  route?: string
}): ReactElement {
  return (
    <article className={cx('requirement-summary-item')}>
      <div className={cx('requirement-summary-item-title')}>
        <Text strong>{name}</Text>
        {route ? <code>{route}</code> : null}
      </div>
      {description ? <Paragraph type="secondary">{description}</Paragraph> : null}
      {labels?.length ? (
        <div className={cx('requirement-summary-tags')}>
          {labels.map((label) => <Tag key={label}>{label}</Tag>)}
        </div>
      ) : null}
    </article>
  )
}

// 渲染单个实体及其字段表，用于需求确认面的数据来源模块。
function EntityCard({ entity }: { entity: SourceEntity }): ReactElement {
  return (
    <article className={cx('requirement-summary-entity')}>
      <Text strong>{entity.name}</Text>
      {entity.description ? (
        <Paragraph type="secondary">{entity.description}</Paragraph>
      ) : null}
      {entity.fields.length ? (
        <div className={cx('requirement-summary-entity-fields')}>
          <div className={cx('requirement-summary-entity-fields-head')}>
            <Text strong>名称</Text>
            <Text strong>说明</Text>
          </div>
          {entity.fields.map((field) => (
            <div className={cx('requirement-summary-entity-field')} key={field.label}>
              <Text strong>{field.label}</Text>
              <Text type="secondary">{field.description || '—'}</Text>
            </div>
          ))}
        </div>
      ) : null}
    </article>
  )
}

// 以默认结构化视图展示 RequirementSpec 中最影响后续规划的信息。
export default function RequirementSpecSummary({ spec }: Props): ReactElement {
  const app = asRecord(spec.app_info) || {}
  const roles = asArray(spec.user_roles)
  const pages = asArray(spec.pages)
  const entities = sourceEntities(spec.entities)
  const flows = asArray(spec.business_flows)
  const assumptions = itemLabels(spec.assumptions)
  const appName = itemText(app, ['name']) || '未命名应用'

  return (
    <div className={cx('requirement-summary')}>
      <section className={cx('requirement-summary-hero')}>
        <div>
          <Text type="secondary">应用名称</Text>
          <Title level={3}>{appName}</Title>
        </div>
      </section>

      {pages.length ? (
        <SummarySection icon={<DesktopOutlined />} title="页面">
          <div className={cx('requirement-summary-grid')}>
            {pages.map((item, index) => {
              const record = asRecord(item)
              return (
                <SummaryItem
                  description={itemText(item, ['description'])}
                  key={itemText(item, ['id', 'name']) || `page-${index}`}
                  labels={itemLabels(record?.components)}
                  name={itemText(item, ['name']) || `页面 ${index + 1}`}
                  route={itemText(item, ['path', 'route', 'route_path', 'routePath'])}
                />
              )
            })}
          </div>
        </SummarySection>
      ) : null}

      {roles.length ? (
        <SummarySection icon={<TeamOutlined />} title="用户角色">
          <div className={cx('requirement-summary-grid')}>
            {roles.map((item, index) => {
              const record = asRecord(item)
              return (
                <SummaryItem
                  description={itemText(item, ['description'])}
                  key={itemText(item, ['id', 'name']) || `role-${index}`}
                  labels={itemLabels(record?.permissions)}
                  name={itemText(item, ['name']) || `角色 ${index + 1}`}
                />
              )
            })}
          </div>
        </SummarySection>
      ) : null}

      {flows.length ? (
        <SummarySection icon={<PartitionOutlined />} title="核心业务流程">
          <div className={cx('requirement-summary-list')}>
            {flows.map((item, index) => {
              const steps = asArray(asRecord(item)?.steps)
              return (
                <article className={cx('requirement-summary-flow')} key={itemText(item, ['id', 'name']) || `flow-${index}`}>
                  <Text strong>{itemText(item, ['name']) || `流程 ${index + 1}`}</Text>
                  {itemText(item, ['description']) ? <Text type="secondary">{itemText(item, ['description'])}</Text> : null}
                  <ol>
                    {steps.map((step, stepIndex) => (
                      <li key={itemText(step, ['step_id']) || `step-${stepIndex}`}>
                        {itemText(step, ['description']) || `步骤 ${stepIndex + 1}`}
                      </li>
                    ))}
                  </ol>
                </article>
              )
            })}
          </div>
        </SummarySection>
      ) : null}

      {entities.length ? (
        <SummarySection icon={<DatabaseOutlined />} title="实体">
          <div className={cx('requirement-summary-grid')}>
            {entities.map((entity) => (
              <EntityCard entity={entity} key={entity.id} />
            ))}
          </div>
        </SummarySection>
      ) : null}

      {assumptions.length ? (
        <SummarySection icon={<FlagOutlined />} title="规划假设">
          <div className={cx('requirement-summary-notes')}>
            {assumptions.map((item) => <Text key={item}><FileTextOutlined />{item}</Text>)}
          </div>
        </SummarySection>
      ) : null}
    </div>
  )
}
