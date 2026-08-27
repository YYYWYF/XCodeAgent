import { SafetyCertificateOutlined } from '@ant-design/icons'
import { Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import { authorizationDesignView, type AuthorizationRoleView } from './TechnicalPlanAuthorizationData'
import type { JsonRecord } from './TechnicalPlanDocPanelData'

const { Text } = Typography

/** 渲染单个角色的默认资源及操作资源关联 Endpoint。 */
function RoleCard({ role }: { role: AuthorizationRoleView }): ReactElement {
  return (
    <article className={cx('technical-plan-authorization-role-card')}>
      <header>
        <div>
          <strong>{role.name}</strong>
          <code>{role.seedKey || '未声明 seed key'}</code>
          {role.description ? <Text type="secondary">{role.description}</Text> : null}
        </div>
        <div className={cx('technical-plan-authorization-role-tags')}>
          {role.isInitialAdminRole ? <Tag>初始管理员</Tag> : null}
          {role.isSystemRole ? <Tag>系统角色</Tag> : null}
        </div>
      </header>
      <Text type="secondary">{role.resourceCount} 项默认资源</Text>
      {role.groups.length ? (
        <details>
          <summary>展开资源明细</summary>
          <div className={cx('technical-plan-authorization-groups')}>
            {role.groups.map((group) => (
              <section key={group.key}>
                <strong>{group.label}</strong>
                {group.resources.map((resource) => (
                  <div className={cx('technical-plan-authorization-resource')} key={resource.key}>
                    <div>
                      <Text strong>{resource.name}</Text>
                      <code>{resource.key}</code>
                    </div>
                    {resource.description ? <Text type="secondary">{resource.description}</Text> : null}
                    {resource.target ? <Text type="secondary">目标：{resource.target}</Text> : null}
                    {resource.sourceRuleIds.length ? <Text type="secondary">来源：{resource.sourceRuleIds.join('、')}</Text> : null}
                    {resource.endpointBindings.map((binding) => (
                      <Text className={cx('technical-plan-authorization-endpoint')} type="secondary" key={binding.endpointId}>
                        Endpoint：{binding.endpointId} · ANY-OF（当前角色拥有：{binding.ownedResourceKeys.join('、')}）
                      </Text>
                    ))}
                  </div>
                ))}
              </section>
            ))}
          </div>
        </details>
      ) : (
        <Text type="secondary">该角色未获得默认资源。</Text>
      )}
    </article>
  )
}

/** 渲染 TechnicalPlan 的默认角色授权模型；不表达成员级运行时权限。 */
export function AuthorizationSection({ plan, sectionKey }: { plan: JsonRecord; sectionKey: string }): ReactElement {
  const authorization = authorizationDesignView(plan)
  return (
    <section
      aria-label="权限"
      className={cx('technical-plan-section')}
      id={`technical-plan-panel-${sectionKey}`}
      role="tabpanel"
    >
      <div className={cx('technical-plan-section-title')}>
        <SafetyCertificateOutlined /> <span>权限设计</span>
      </div>
      <Text className={cx('technical-plan-authorization-note')} type="secondary">
        页面权限与 Endpoint 权限相互独立；未显式授权的 Endpoint 按当前契约默认可访问。本视图仅展示默认角色授权。
      </Text>
      {authorization?.roles.length ? (
        <div className={cx('technical-plan-authorization-role-grid')}>
          {authorization.roles.map((role) => <RoleCard key={role.seedKey || role.name} role={role} />)}
        </div>
      ) : (
        <Text type="secondary">暂无默认角色授权。</Text>
      )}
    </section>
  )
}
