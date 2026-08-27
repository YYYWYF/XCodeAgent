import {
  DesktopOutlined,
  FileTextOutlined,
  FlagOutlined,
  PartitionOutlined,
  SafetyCertificateOutlined,
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
    ? (value as Record<string, unknown>)
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
    .map((item) => (typeof item === 'string' ? item : itemText(item, ['name', 'label'])))
    .filter(Boolean)
}

// 读取需求文档中的权限候选，缺失时按关闭权限处理。
function authorizationRequirements(value: unknown): Record<string, unknown> {
  const record = asRecord(value)
  return record || {}
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
          {labels.map((label) => (
            <Tag key={label}>{label}</Tag>
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
  const flows = asArray(spec.business_flows)
  const authorization = authorizationRequirements(spec.authorization_requirements)
  const authorizationEnabled = authorization.enabled === true
  const restrictedPages = asArray(authorization.restrictedPages)
  const restrictedOperations = asArray(authorization.restrictedOperations)
  const dataRules = asArray(authorization.dataRules)
  const roleNames = new Map(
    roles.map((role) => [itemText(role, ['id']), itemText(role, ['name', 'id'])])
  )
  // 将规则的默认授权角色 ID 映射为确认界面可读名称。
  const grantedRoleLabels = (item: unknown): string[] => {
    const record = asRecord(item)
    const roleIds = asArray(record?.defaultGrantedRoleIds)
    return roleIds
      .map((roleId) => roleNames.get(itemText(roleId, ['id'])) || itemText(roleId, ['id']))
      .filter(Boolean)
  }
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
        <SummarySection icon={<TeamOutlined />} title="业务参与者">
          <div className={cx('requirement-summary-grid')}>
            {roles.map((item, index) => {
              return (
                <SummaryItem
                  description={itemText(item, ['description'])}
                  key={itemText(item, ['id', 'name']) || `role-${index}`}
                  labels={[
                    ...(asRecord(item)?.isInitialAdminRole === true ? ['初始系统管理员'] : []),
                    ...(asRecord(item)?.isSystemRole === true ? ['系统角色'] : [])
                  ]}
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
                <article
                  className={cx('requirement-summary-flow')}
                  key={itemText(item, ['id', 'name']) || `flow-${index}`}
                >
                  <Text strong>{itemText(item, ['name']) || `流程 ${index + 1}`}</Text>
                  {itemText(item, ['description']) ? (
                    <Text type="secondary">{itemText(item, ['description'])}</Text>
                  ) : null}
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

      <SummarySection icon={<SafetyCertificateOutlined />} title="权限需求">
        {!authorizationEnabled ? (
          <Text type="secondary">不涉及应用级资源授权。</Text>
        ) : (
          <div className={cx('requirement-summary-list')}>
            <Text type="secondary">
              权限关系遵循 RBAC
              资源模型；本需求确认首次默认角色授权和初始系统管理员角色，运行态关系仍可动态配置。
            </Text>
            <Text type="secondary">
              身份认证不自动产生 RBAC 资源；下面仅列出用户需求明确提及的受控业务对象。
            </Text>
            <Text type="secondary">
              未提出的业务功能默认不受 RBAC 控制，对已认证成员保持可见可用。
            </Text>
            <SummaryItem
              description={`受控页面 ${restrictedPages.length} 项、受控操作 ${restrictedOperations.length} 项、数据范围 ${dataRules.length} 项。`}
              labels={['页面和操作入口无权限时固定隐藏；直接访问返回 403']}
              name="权限候选"
            />
            {!restrictedPages.length && !restrictedOperations.length && !dataRules.length ? (
              <Text type="secondary">
                用户需求未提出具体页面、操作或数据范围权限控制，业务资源候选保持为空。
              </Text>
            ) : null}
            {restrictedPages.map((item, index) => (
              <SummaryItem
                description={itemText(item, ['description', 'rationale']) || '待补充业务说明'}
                key={itemText(item, ['name']) || `restricted-page-${index}`}
                labels={grantedRoleLabels(item)}
                name={itemText(item, ['name']) || `受控页面 ${index + 1}`}
              />
            ))}
            {restrictedOperations.map((item, index) => (
              <SummaryItem
                description={itemText(item, ['description', 'rationale']) || '待补充业务说明'}
                key={itemText(item, ['name']) || `restricted-operation-${index}`}
                labels={grantedRoleLabels(item)}
                name={itemText(item, ['name']) || `受控操作 ${index + 1}`}
              />
            ))}
            {dataRules.map((item, index) => (
              <SummaryItem
                description={
                  `包含：${itemText(item, ['includes']) || '待补充'}；不包含：${itemText(item, ['excludes']) || '待补充'}`
                }
                key={itemText(item, ['name']) || `data-rule-${index}`}
                labels={grantedRoleLabels(item)}
                name={itemText(item, ['name']) || `数据范围 ${index + 1}`}
              />
            ))}
            <Text type="secondary">系统固定页面：/roles（权限管理），由模板提供，不属于业务页面清单。</Text>
          </div>
        )}
      </SummarySection>

      {assumptions.length ? (
        <SummarySection icon={<FlagOutlined />} title="规划假设">
          <div className={cx('requirement-summary-notes')}>
            {assumptions.map((item) => (
              <Text key={item}>
                <FileTextOutlined />
                {item}
              </Text>
            ))}
          </div>
        </SummarySection>
      ) : null}
    </div>
  )
}
