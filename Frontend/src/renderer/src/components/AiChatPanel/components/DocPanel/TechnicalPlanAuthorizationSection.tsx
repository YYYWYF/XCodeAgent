import { SafetyCertificateOutlined } from '@ant-design/icons'
import { Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import { endpointAuthorizationView } from './TechnicalPlanAuthorizationData'
import type { JsonRecord } from './TechnicalPlanDocPanelData'
import './AuthorizationSections.less'

const { Text } = Typography

/** 只展示本阶段新增的接口权限绑定；业务授权修改引导回需求阶段。 */
export function AuthorizationSection({ plan, productPlan, sectionKey, onReviseAuthorization }: {
  plan: JsonRecord; productPlan: JsonRecord; sectionKey: string; onReviseAuthorization?: () => void
}): ReactElement {
  const endpoints = endpointAuthorizationView(plan, productPlan)
  return (
    <section aria-label="接口权限绑定" className={cx('technical-plan-section', 'authorization-stage-section')}
      id={`technical-plan-panel-${sectionKey}`} role="tabpanel">
      <div className={cx('technical-plan-section-title')}><SafetyCertificateOutlined /> <span>接口权限绑定</span></div>
      <Text type="secondary">根据业务操作绑定接口权限，与页面权限独立。角色及默认权限需返回需求阶段修改并重新确认。</Text>
      {onReviseAuthorization ? <button type="button" className={cx('authorization-stage-action')}
        onClick={onReviseAuthorization}>修改角色与权限</button> : null}
      <div className={cx('authorization-stage-cards')}>
        {endpoints.map((endpoint) => <article className={cx('authorization-stage-card')} key={endpoint.id}>
          <strong>{endpoint.method} {endpoint.path} · {endpoint.name}</strong>
          <code>{endpoint.id}</code>
          <Text>业务操作：{endpoint.operations.join('、') || '未关联业务操作'}</Text>
          {!endpoint.permissionsKnown ? <Text type="warning">权限绑定尚未就绪，请检查当前技术规划。</Text> : endpoint.resources.length ? <>
            <Text type="secondary">拥有下列任一操作资源即可通过资源权限检查（ANY-OF）：</Text>
            {endpoint.resources.map((resource) => <div className={cx('authorization-stage-resource')} key={resource.key}>
              <Text>{resource.name}</Text><code>{resource.key}</code>
            </div>)}
          </> : <Text type="secondary">未设置资源权限控制，接口登录要求单独生效。</Text>}
        </article>)}
        {!endpoints.length ? <Text type="secondary">暂无业务接口，无需绑定接口权限。</Text> : null}
      </div>
    </section>
  )
}
