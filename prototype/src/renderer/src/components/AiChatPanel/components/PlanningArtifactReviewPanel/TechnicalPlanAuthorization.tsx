import { SafetyCertificateOutlined } from '@ant-design/icons'
import { Empty, Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import type { ProjectedEndpoint } from './AppApiContractsProjection'

const { Text } = Typography

/** 权限页签中的单个角色视图：需求角色 + 权限到接口的映射结果。 */
type AuthorizationRoleView = {
  id: string
  name: string
  description: string
  isSystemRole: boolean
  permissions: Array<{ name: string; endpoints: Array<{ id: string; label: string }> }>
}

/** 去掉“查看/查询”前缀后比较权限与操作名，吸收需求用词与操作命名的差异。 */
function normalizePermissionName(name: string): string {
  return name.replace(/^(查看|查询)/, '')
}

/** 把需求角色权限映射到契约接口：命中时返回 METHOD path 标签，未命中保持待开发绑定。 */
function permissionEndpoints(
  permission: string,
  endpoints: ProjectedEndpoint[]
): Array<{ id: string; label: string }> {
  const wanted = normalizePermissionName(permission)
  const hits: Array<{ id: string; label: string }> = []
  endpoints.forEach((endpoint) => {
    const operationName = normalizePermissionName(endpoint.operationName)
    // 仅在双方都非空时做双向包含匹配：避免“提交回检”这类只共享“回检”二字的权限误命中。
    const hit =
      Boolean(wanted) &&
      Boolean(operationName) &&
      (operationName === wanted ||
        operationName.includes(wanted) ||
        wanted.includes(operationName))
    if (wanted && hit)
      hits.push({ id: endpoint.id, label: `${endpoint.method} ${endpoint.path}` })
  })
  return hits
}

/** 从需求说明书的 user_roles 投影权限角色视图；不补写需求之外的角色事实。 */
function authorizationRoles(
  requirementSpec: Record<string, unknown>,
  endpoints: ProjectedEndpoint[]
): AuthorizationRoleView[] {
  const roles = Array.isArray(requirementSpec.user_roles) ? requirementSpec.user_roles : []
  return roles.map((role) => {
    const record = (role && typeof role === 'object' ? role : {}) as Record<string, unknown>
    const permissions = Array.isArray(record.permissions) ? record.permissions : []
    return {
      id: String(record.id || ''),
      name: String(record.name || record.id || '未命名角色'),
      description: String(record.description || ''),
      isSystemRole: record.isSystemRole === true,
      permissions: permissions.map((permission) => ({
        name: String(permission),
        endpoints: permissionEndpoints(String(permission), endpoints)
      }))
    }
  })
}

/**
 * 技术规划方案「权限」页签：展示需求角色到默认操作授权的投影。
 * 页面权限与操作权限相互独立；未显式授权的接口按契约默认可访问。
 */
export function AuthorizationSection({
  endpoints,
  requirementSpec
}: {
  endpoints: ProjectedEndpoint[]
  requirementSpec: Record<string, unknown>
}): ReactElement {
  const roles = authorizationRoles(requirementSpec, endpoints)
  if (!roles.length) {
    return <Empty description="需求说明书中尚未定义用户角色" image={Empty.PRESENTED_IMAGE_SIMPLE} />
  }
  return (
    <section>
      <div className={cx('planning-authorization-roles')}>
        {roles.map((role) => (
          <article className={cx('planning-authorization-role')} key={role.id || role.name}>
            <header>
              <div>
                <strong>{role.name}</strong>
                {role.description ? <Text type="secondary">{role.description}</Text> : null}
              </div>
              {role.isSystemRole ? <Tag>系统角色</Tag> : null}
            </header>
            <div className={cx('planning-authorization-permissions')}>
              {role.permissions.length ? (
                role.permissions.map((permission) => (
                  <div className={cx('planning-authorization-permission')} key={permission.name}>
                    <strong>{permission.name}</strong>
                    {permission.endpoints.length ? (
                      <div>
                        {permission.endpoints.map((endpoint) => (
                          <code key={endpoint.id}>{endpoint.label}</code>
                        ))}
                      </div>
                    ) : (
                      <Text type="secondary">待开发阶段实现时绑定具体接口</Text>
                    )}
                  </div>
                ))
              ) : (
                <Text type="secondary">该角色未声明具体权限。</Text>
              )}
            </div>
          </article>
        ))}
      </div>
      <Text type="secondary">
        <SafetyCertificateOutlined />{' '}
        页面权限与操作权限相互独立；未显式授权的接口按契约默认可访问。此处为需求角色到默认操作授权的投影。
      </Text>
    </section>
  )
}
