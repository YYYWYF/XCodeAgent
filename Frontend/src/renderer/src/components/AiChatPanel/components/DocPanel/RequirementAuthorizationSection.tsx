import { useEffect, useState, type ReactElement } from 'react'
import { Alert, Spin, Typography } from 'antd'
import { SafetyCertificateOutlined } from '@ant-design/icons'
import { cx } from '../../../../utils'
import { readRequirementAuthorization, type RequirementAuthorizationPreview } from '../../../../service/requirementAuthorization'
import { requirementAuthorizationRoles } from './RequirementAuthorizationData'
import type { JsonRecord } from './TechnicalPlanDocPanelData'
import './AuthorizationSections.less'

const { Text } = Typography

/** 在第一阶段展示角色和资源默认授权；接口绑定留给技术规划，不读取 TechnicalPlan。 */
export default function RequirementAuthorizationSection({ spec, productPlan, workspaceRoot }: {
  spec: JsonRecord; productPlan: JsonRecord; workspaceRoot?: string
}): ReactElement {
  const inputKey = JSON.stringify([workspaceRoot, spec, productPlan])
  const [state, setState] = useState<{ key: string; data?: RequirementAuthorizationPreview; error?: string }>()
  const current = state?.key === inputKey ? state : undefined
  useEffect(() => {
    if (!workspaceRoot) return
    const controller = new AbortController()
    void readRequirementAuthorization(workspaceRoot, spec, productPlan, controller.signal).then(
      (data) => { if (!controller.signal.aborted) setState({ key: inputKey, data }) },
      (error: unknown) => {
        if (!controller.signal.aborted) setState({ key: inputKey, error: error instanceof Error ? error.message : '角色与权限读取失败。' })
      }
    )
    return () => controller.abort()
    // 输入内容变化才重编译，避免无关工作流进度反复读取；旧结果不覆盖新草稿。
  }, [inputKey])
  const roles = requirementAuthorizationRoles(spec, productPlan, current?.data?.manifest)
  return (
    <section aria-label="角色与权限" className={cx('requirement-doc-section', 'authorization-stage-section')}
      id="requirement-doc-panel-authorization" role="tabpanel">
      <div className={cx('requirement-doc-section-title')}><SafetyCertificateOutlined /> <span>角色与权限</span></div>
      {!workspaceRoot ? <Alert type="warning" showIcon message="缺少工作区，暂时无法读取权限配置。" />
        : current?.error ? <Alert type="error" showIcon message="权限资源尚未就绪" description={current.error} />
          : !current ? <Spin tip="正在读取角色与权限…" />
            : !current.data?.enabled ? <Text type="secondary">权限控制未启用，以下仅展示业务角色。</Text>
              : !current.data.ready ? <Text type="secondary">页面与操作生成后显示默认权限，请一并确认。</Text>
                : <Text type="secondary">页面与操作分别授权；未设权限要求的功能不受限制。</Text>}
      <div className={cx('authorization-stage-cards')}>
        {roles.map((role) => (
          <article className={cx('authorization-stage-card')} key={role.key}>
            <strong>{role.name}</strong>
            {role.description ? <Text type="secondary">{role.description}</Text> : null}
            {current?.data?.enabled && current.data.ready ? <>
              <Text type="secondary">默认拥有 {role.resources.length} 项权限</Text>
              {role.resources.map((resource) => <div className={cx('authorization-stage-resource')} key={resource.key}>
                <Text>{resource.permissionDescription}</Text>
                {resource.description ? <Text type="secondary">{resource.description}</Text> : null}
              </div>)}
              {!role.resources.length ? <Text type="secondary">该角色暂无默认授予的权限。</Text> : null}
            </> : null}
          </article>
        ))}
        {!roles.length ? <Text type="secondary">暂无业务角色。</Text> : null}
      </div>
    </section>
  )
}
