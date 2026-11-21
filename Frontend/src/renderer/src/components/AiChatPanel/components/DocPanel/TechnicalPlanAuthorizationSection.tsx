import { SafetyCertificateOutlined } from '@ant-design/icons'
import { Typography } from 'antd'
import { useMemo } from 'react'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import { endpointAuthorizationView } from './TechnicalPlanAuthorizationData'
import type { JsonRecord } from './TechnicalPlanDocPanelData'
import {
  TechnicalPlanChangeBadge,
  type TechnicalPlanChangeStatus
} from './TechnicalPlanDocSections'
import './AuthorizationSections.less'

const { Text } = Typography

/** 接口权限绑定 tab：展示每个接口的业务操作与资源绑定，并标注相对基线的变更。 */
export function AuthorizationSection({ plan, productPlan, sectionKey, changeKinds, hasBaseline, onReviseAuthorization }: {
  plan: JsonRecord
  productPlan: JsonRecord
  sectionKey: string
  /** 各对象的变更种类，键形如 `endpoint:{contractId}:{endpointId}`。 */
  changeKinds?: Record<string, string>
  /** 是否存在上一迭代基线；首轮创建时全部为新增。 */
  hasBaseline?: boolean
  onReviseAuthorization?: () => void
}): ReactElement {
  const endpoints = endpointAuthorizationView(plan, productPlan)
  // 接口权限按接口身份对齐：与 API 契约 tab 的接口标记共用同一个 key，
  // 保证"接口在契约里是新增"时，这里也标新增，不会两处结论打架。
  const withStatus = useMemo(
    () =>
      endpoints.map((endpoint) => {
        // 与 API 契约 tab 共用同一变更键：`endpoint:{contractId}:{endpointId}`，
        // 保证"接口在契约里是新增"时这里也标新增，不会两处结论打架。
        const kind = changeKinds?.[`endpoint:${endpoint.contractId}:${endpoint.id}`]
        return { endpoint, kind }
      }),
    [changeKinds, endpoints]
  )
  const changed = withStatus.filter((item) => item.kind)
  const unchanged = hasBaseline ? withStatus.filter((item) => !item.kind) : []
  const showGroups = changed.length > 0 && unchanged.length > 0

  /** 渲染单个接口的权限绑定卡片；徽标状态与 API 契约 tab 同源。 */
  const renderCard = (item: (typeof withStatus)[number]): ReactElement => {
    const { endpoint, kind } = item
    return (
      <article className={cx('authorization-stage-card')} key={endpoint.id}>
        <div className={cx('authorization-stage-card-heading')}>
          <strong>{endpoint.method} {endpoint.path} · {endpoint.name}</strong>
          <TechnicalPlanChangeBadge
            status={
              kind
                ? ((kind === '新增' ? 'added' : kind === '删除' ? 'deleted' : 'modified') as TechnicalPlanChangeStatus)
                : hasBaseline
                  ? 'unchanged'
                  : undefined
            }
          />
        </div>
        <code>{endpoint.id}</code>
        <Text>业务操作：{endpoint.operations.join('、') || '未关联业务操作'}</Text>
        {!endpoint.permissionsKnown ? <Text type="warning">权限绑定尚未就绪，请检查当前技术规划。</Text> : endpoint.resources.length ? <>
          <Text type="secondary">拥有下列任一操作资源即可通过资源权限检查（ANY-OF）：</Text>
          {endpoint.resources.map((resource) => <div className={cx('authorization-stage-resource')} key={resource.key}>
            <Text>{resource.name}</Text><code>{resource.key}</code>
          </div>)}
        </> : <Text type="secondary">未设置资源权限控制，接口登录要求单独生效。</Text>}
      </article>
    )
  }

  return (
    <section aria-label="接口权限绑定" className={cx('technical-plan-section', 'authorization-stage-section')}
      id={`technical-plan-panel-${sectionKey}`} role="tabpanel">
      <div className={cx('technical-plan-section-title')}>
        <SafetyCertificateOutlined /> <span>接口权限绑定</span>
        {hasBaseline ? (
          <TechnicalPlanChangeBadge status={changeKinds?.['authorization'] ? 'modified' : 'unchanged'} />
        ) : null}
      </div>
      <Text type="secondary">根据业务操作绑定接口权限，与页面权限独立。角色及默认权限需返回需求阶段修改并重新确认。</Text>
      {onReviseAuthorization ? <button type="button" className={cx('authorization-stage-action')}
        onClick={onReviseAuthorization}>修改角色与权限</button> : null}
      <div className={cx('authorization-stage-cards')}>
        {!endpoints.length ? <Text type="secondary">暂无业务接口，无需绑定接口权限。</Text>
          : showGroups ? (
            <>
              <div className={cx('technical-plan-binding-group', 'is-changed')}>
                <div className={cx('requirement-doc-page-group-title')}>
                  <span className={cx('requirement-doc-page-group-badge', 'is-changed')}>
                    本次迭代变更（{changed.length}）
                  </span>
                </div>
                <div className={cx('authorization-stage-cards')}>
                  {changed.map((item) => renderCard(item))}
                </div>
              </div>
              <div className={cx('technical-plan-binding-group', 'is-unchanged')}>
                <div className={cx('requirement-doc-page-group-title')}>
                  <span className={cx('requirement-doc-page-group-badge', 'is-unchanged')}>
                    未变更内容（{unchanged.length}）
                  </span>
                </div>
                <div className={cx('authorization-stage-cards')}>
                  {unchanged.map((item) => renderCard(item))}
                </div>
              </div>
            </>
          ) : withStatus.map((item) => renderCard(item))}
      </div>
    </section>
  )
}
