import RequirementApis from './RequirementApis'
import { Empty, Typography } from 'antd'
import type { TabsProps } from 'antd'
import type { InitializationPlanningArtifacts } from '../../../../planning/model'
import { cx } from '../../../../utils'

const { Paragraph, Text, Title } = Typography
export const STATE_LABELS = { loading: '加载中', empty: '无数据', error: '失败', success: '成功', validation: '输入校验' }

/** 页面行为的展示类型文案。 */
function behaviorLabel(type?: string): string {
  if (type === 'navigation') return '页面跳转'
  if (type === 'interface') return '界面交互'
  return '业务操作'
}

/**
 * 构建审阅态六个分栏的内容：分栏键与编辑态一致，由外层统一的 Tabs 承载。
 * 全部分栏采用平铺文档排版——每个分栏只用一级 h5 标题，内容用「标签：内容」文档行与有序/无序清单，
 * 不使用彩色卡片、指标块或标签芯片。
 */
export function buildRequirementReviewItems(artifacts: InitializationPlanningArtifacts): TabsProps['items'] {
  const spec = artifacts.requirementSpec
  const product = artifacts.productPlan
  const roles = spec.user_roles || []
  const pages = product.pages || []
  const authorization = spec.authorization_requirements || {}
  return [
    { key: 'overview', label: '概览', children: <div className={cx('planning-review-document')}>
      <section>
        <Title level={5}>应用定位</Title>
        <Paragraph><Text strong>{spec.app_info?.name}</Text></Paragraph>
        <Paragraph>{spec.app_info?.target || spec.app_info?.description || spec.app_info?.summary}</Paragraph>
        <Paragraph type="secondary">{roles.length} 个角色 · {pages.length} 个应用页面 · {(spec.apis || []).length} 个应用API</Paragraph>
      </section>
      <section>
        <Title level={5}>业务参与者</Title>
        <ul>
          {roles.map((role: any) => (
            <li key={role.id}>
              <Text strong>{role.name}</Text>：{role.description}
              {(role.permissions || []).length > 0 ? `；职责：${(role.permissions || []).join('、')}` : ''}
            </li>
          ))}
        </ul>
      </section>
      {(spec.feature_modules || []).length > 0 && (
        <section>
          <Title level={5}>功能模块</Title>
          <ul>
            {spec.feature_modules.map((module: any) => (
              <li key={module.id}>
                <Text strong>{module.name}</Text>（{module.priority || '必需'}）：{module.description}
              </li>
            ))}
          </ul>
        </section>
      )}
      {(spec.business_constraints || []).length > 0 && <section><Title level={5}>业务约束</Title><ul>{spec.business_constraints.map((item: string, index: number) => <li key={index}>{item}</li>)}</ul></section>}
      {(spec.assumptions || []).length > 0 && <section><Title level={5}>前提条件</Title><ul>{spec.assumptions.map((item: string, index: number) => <li key={index}>{item}</li>)}</ul></section>}
    </div> },
    { key: 'apis', label: 'API契约', children: <RequirementApis apis={spec.apis || []} pages={spec.pages || []} /> },
    { key: 'pages', label: '应用页面与操作', children: <div className={cx('planning-review-document')}>
      {pages.map((page: any) => (
        <section key={page.pageId}>
          <Title level={5}>
            {page.name}
            {page.path ? <Text code> {page.path}</Text> : null}
          </Title>
          <Paragraph>{page.description}</Paragraph>
          {page.goal ? <Paragraph><Text strong>页面目标：</Text>{page.goal}</Paragraph> : null}
          {(page.information_items || []).length > 0 && (
            <Paragraph>
              <Text strong>业务信息：</Text>
              {(page.information_items || []).map((item: any) => `${item.label}${item.description ? `（${item.description}）` : ''}`).join('；')}
            </Paragraph>
          )}
          {(page.actions || []).length > 0 && (
            <>
              <Paragraph><Text strong>用户操作</Text></Paragraph>
              <ul>
                {(page.actions || []).map((action: any) => {
                  const targetName = action.behavior?.targetPageId
                    ? pages.find((item: any) => item.pageId === action.behavior.targetPageId)?.name || action.behavior.targetPageId
                    : ''
                  return (
                    <li key={action.actionId}>
                      <Text strong>{action.name}</Text>（{behaviorLabel(action.behavior?.type)}）：{action.description || '—'}
                      {action.behavior?.expectedResult ? `；预期结果：${action.behavior.expectedResult}` : ''}
                      {targetName ? `；目标页面：${targetName}` : ''}
                      {action.requiresConfirmation ? '；执行前需要用户确认' : ''}
                    </li>
                  )
                })}
              </ul>
            </>
          )}
          {Object.entries(STATE_LABELS).some(([key]) => page.state_requirements?.[key]) && (
            <Paragraph>
              <Text strong>页面状态：</Text>
              {Object.entries(STATE_LABELS)
                .map(([key, label]) => (page.state_requirements?.[key] ? `${label}：${page.state_requirements[key]}` : ''))
                .filter(Boolean)
                .join('；')}
            </Paragraph>
          )}
          {(page.acceptance_criteria || []).length > 0 && (
            <>
              <Paragraph><Text strong>页面验收标准</Text></Paragraph>
              <ul>{(page.acceptance_criteria || []).map((item: string, index: number) => <li key={index}>{item}</li>)}</ul>
            </>
          )}
        </section>
      ))}
    </div> },
    { key: 'flows', label: '业务流程', children: <div className={cx('planning-review-document')}>
      {(spec.business_flows || []).map((flow: any) => (
        <section key={flow.id}>
          <Title level={5}>{flow.name}</Title>
          <Paragraph>{flow.description}</Paragraph>
          <ol>{(flow.steps || []).map((step: any, index: number) => <li key={index}>{typeof step === 'string' ? step : step.description}</li>)}</ol>
        </section>
      ))}
    </div> },
    { key: 'authorization', label: '权限需求', children: <div className={cx('planning-review-document')}>
      {authorization.enabled ? (
        <>
          <Paragraph>初始系统管理员：{roles.find((role: any) => role.id === authorization.initialAdminRoleId)?.name || '尚未指定'}</Paragraph>
          {[['restrictedPages', '受控页面'], ['restrictedOperations', '受控操作']].map(([key, title]) => (
            <section key={key}>
              <Title level={5}>{title}</Title>
              <ul>
                {(authorization[key] || []).map((rule: any, index: number) => (
                  <li key={rule.ruleId || index}>
                    <Text strong>{rule.name}</Text>：{rule.description}
                    {rule.rationale ? `；限制理由：${rule.rationale}` : ''}
                    {(rule.defaultGrantedRoleIds || []).length > 0
                      ? `；默认授权：${(rule.defaultGrantedRoleIds || []).map((id: string) => roles.find((role: any) => role.id === id)?.name || id).join('、')}`
                      : ''}
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </>
      ) : (
        <Empty description="此应用未开启资源授权" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      )}
    </div> },
    { key: 'acceptance', label: '验收标准', children: <div className={cx('planning-review-document')}><section><Title level={5}>应用验收标准</Title><ul>{[...new Set<string>([...(spec.acceptance_criteria || []), ...(product.product_acceptance_criteria || [])])].map((item, index) => <li key={index}>{item}</li>)}</ul></section></div> }
  ]
}
