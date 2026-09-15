import RequirementBusinessObjects from './RequirementBusinessObjects'
import { Alert, Empty, Tag, Typography } from 'antd'
import type { TabsProps } from 'antd'
import type { InitializationPlanningArtifacts } from '../../../../planning/model'
import { requirementReadinessIssues } from '../../../../planning/requirementQuality'
import { cx } from '../../../../utils'

const { Title, Paragraph, Text } = Typography
export const STATE_LABELS = { loading: '加载中', empty: '无数据', error: '失败', success: '成功', validation: '输入校验' }

/** 构建审阅态六个分栏的内容：分栏键与编辑态一致，由外层统一的 Tabs 承载。 */
export function buildRequirementReviewItems(artifacts: InitializationPlanningArtifacts, confirmed = false): TabsProps['items'] {
  const spec = artifacts.requirementSpec
  const product = artifacts.productPlan
  const roles = spec.user_roles || []
  const pages = product.pages || []
  const authorization = spec.authorization_requirements || {}
  const readinessIssues = requirementReadinessIssues(spec, product)
  return [
    { key: 'overview', label: '概览', children: <div className={cx('planning-review-document')}>
      {!confirmed && <Alert showIcon type={readinessIssues.length ? 'warning' : 'success'} message={readinessIssues.length ? `下游就绪检查：还需补齐 ${readinessIssues.length} 项` : '下游就绪检查：需求规格完整'} description={readinessIssues.length ? readinessIssues.slice(0, 3).map((item) => `${item.label}：${item.detail}`).join('；') : '已覆盖应用定位、角色、模块、页面、实体、流程、页面行为、权限和验收标准。'} />}
      <section className={cx('planning-review-hero')}><Text type="secondary">应用定位</Text><Title level={4}>{spec.app_info?.name}</Title><Paragraph>{spec.app_info?.target || spec.app_info?.description || spec.app_info?.summary}</Paragraph></section>
      <div className={cx('planning-review-metrics')}><span><strong>{roles.length}</strong> 个角色</span><span><strong>{pages.length}</strong> 个页面</span><span><strong>{(spec.entities || []).length}</strong> 个实体</span></div>
      <section><Title level={5}>业务参与者</Title><div className={cx('planning-review-collection')}>{roles.map((role: any) => <article key={role.id}><Text strong>{role.name}</Text><Paragraph>{role.description}</Paragraph>{(role.permissions || []).map((permission: string) => <Tag key={permission}>{permission}</Tag>)}</article>)}</div></section>
      {(spec.feature_modules || []).length > 0 && <section><Title level={5}>功能模块</Title><div className={cx('planning-review-collection')}>{spec.feature_modules.map((module: any) => <article key={module.id}><Text strong>{module.name}</Text><Tag>{module.priority || '必需'}</Tag><Paragraph>{module.description}</Paragraph></article>)}</div></section>}
      {(spec.business_constraints || []).length > 0 && <section><Title level={5}>业务约束</Title><ul>{spec.business_constraints.map((item: string, index: number) => <li key={index}>{item}</li>)}</ul></section>}
      {(spec.assumptions || []).length > 0 && <section><Title level={5}>前提条件</Title><ul>{spec.assumptions.map((item: string, index: number) => <li key={index}>{item}</li>)}</ul></section>}
    </div> },
    { key: 'pages', label: '页面与操作', children: <div className={cx('planning-review-document', 'planning-review-collection')}>{pages.map((page: any) => <article key={page.pageId}>
      <div className={cx('planning-review-item-title')}><Text strong>{page.name}</Text><Text code>{page.path}</Text></div><Paragraph>{page.description}</Paragraph><Paragraph><Text strong>页面目标：</Text>{page.goal}</Paragraph>
      <Title level={5}>业务信息</Title><ul>{(page.information_items || []).map((item: any) => <li key={item.itemId}><Text strong>{item.label}</Text>：{item.description}</li>)}</ul>
      <Title level={5}>用户操作</Title>{(page.actions || []).map((action: any) => <section className={cx('requirement-review-action')} key={action.actionId}><Text strong>{action.name}</Text><Tag>{action.behavior?.type === 'navigation' ? '页面跳转' : action.behavior?.type === 'interface' ? '界面交互' : '业务操作'}</Tag><Paragraph>{action.description}</Paragraph><Paragraph>预期结果：{action.behavior?.expectedResult}</Paragraph>{action.behavior?.targetPageId && <Paragraph>目标页面：{pages.find((item: any) => item.pageId === action.behavior.targetPageId)?.name || action.behavior.targetPageId}</Paragraph>}{action.requiresConfirmation && <Text type="secondary">执行前需要用户确认</Text>}</section>)}
      <Title level={5}>页面状态</Title><dl className={cx('requirement-review-states')}>{Object.entries(STATE_LABELS).map(([key, label]) => page.state_requirements?.[key] ? <div key={key}><dt>{label}</dt><dd>{page.state_requirements[key]}</dd></div> : null)}</dl>
      <Title level={5}>页面验收标准</Title><ul>{(page.acceptance_criteria || []).map((item: string, index: number) => <li key={index}>{item}</li>)}</ul>
    </article>)}</div> },
    { key: 'flows', label: '业务流程', children: <div className={cx('planning-review-document', 'planning-review-collection')}>{(spec.business_flows || []).map((flow: any) => <article key={flow.id}><Text strong>{flow.name}</Text><Paragraph>{flow.description}</Paragraph><ol>{(flow.steps || []).map((step: any, index: number) => <li key={index}>{typeof step === 'string' ? step : step.description}</li>)}</ol></article>)}</div> },
    { key: 'entities', label: '实体', children: <RequirementBusinessObjects entities={spec.entities || []} pages={spec.pages || []} /> },
    { key: 'authorization', label: '权限需求', children: <div className={cx('planning-review-document')}>
      {authorization.enabled ? <><Paragraph>初始系统管理员：{roles.find((role: any) => role.id === authorization.initialAdminRoleId)?.name || '尚未指定'}</Paragraph>{[['restrictedPages', '受控页面'], ['restrictedOperations', '受控操作']].map(([key, title]) => <section key={key}><Title level={5}>{title}</Title><div className={cx('planning-review-collection')}>{(authorization[key] || []).map((rule: any, index: number) => <article key={rule.ruleId || index}><Text strong>{rule.name}</Text><Paragraph>{rule.description}</Paragraph><Paragraph>限制理由：{rule.rationale}</Paragraph><Paragraph>默认授权：{(rule.defaultGrantedRoleIds || []).map((id: string) => roles.find((role: any) => role.id === id)?.name || id).join('、')}</Paragraph></article>)}</div></section>)}</> : <Empty description="此应用未开启资源授权" image={Empty.PRESENTED_IMAGE_SIMPLE} />}
    </div> },
    { key: 'acceptance', label: '验收标准', children: <div className={cx('planning-review-document')}><Title level={5}>应用验收标准</Title><ul>{[...new Set<string>([...(spec.acceptance_criteria || []), ...(product.product_acceptance_criteria || [])])].map((item, index) => <li key={index}>{item}</li>)}</ul></div> }
  ]
}
