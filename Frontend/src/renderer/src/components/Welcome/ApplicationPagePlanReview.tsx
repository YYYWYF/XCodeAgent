import {
  ApiOutlined,
  AppstoreOutlined,
  LinkOutlined,
  ThunderboltOutlined
} from '@ant-design/icons'
import { Tag, Typography } from 'antd'
import type { ApplicationPagePlan } from '../../typings'
import { cx } from '../../utils'

const { Paragraph, Text, Title } = Typography

type Props = {
  plan: ApplicationPagePlan
}

// 以模块卡片展示待确认的页面目录、页面关系、用户交互与 API 功能契约。
export default function ApplicationPagePlanReview({ plan }: Props): JSX.Element {
  const pageNames = new Map(plan.pages.map((page) => [page.id, page.name]))
  const apiNames = new Map(plan.apis.map((api) => [api.id, api.name]))

  return (
    <section className={cx('page-plan-design')}>
      <header className={cx('page-plan-section-header')}>
        <span className={cx('page-plan-section-icon')}><AppstoreOutlined /></span>
        <div>
          <Title level={4}>页面目录与功能设计</Title>
          <Paragraph type="secondary">每个区块代表一个独立页面，包含职责、关联资源和完整交互路径。</Paragraph>
        </div>
        <span className={cx('page-plan-section-count')}>{plan.pages.length} 个页面</span>
      </header>

      <div className={cx('page-plan-card-grid')}>
        {plan.pages.map((page, pageIndex) => (
          <article className={cx('page-plan-card')} key={page.id}>
            <header className={cx('page-plan-card-header')}>
              <span className={cx('page-plan-card-index')}>{String(pageIndex + 1).padStart(2, '0')}</span>
              <div className={cx('page-plan-card-identity')}>
                <Title level={5}>{page.name}</Title>
                <Text code>{page.path}</Text>
              </div>
              <div className={cx('page-plan-card-stats')}>
                <span>{page.interactions.length} 个交互</span>
                <span>{page.apiIds.length} 个 API</span>
              </div>
            </header>

            <div className={cx('page-plan-purpose')}>
              <Text className={cx('page-plan-module-label')}>页面职责</Text>
              <Paragraph>{page.purpose}</Paragraph>
            </div>

            <section className={cx('page-plan-module')}>
              <Text className={cx('page-plan-module-label')}>核心功能</Text>
              <div className={cx('page-plan-feature-list')}>
                {page.keyFeatures.map((feature) => (
                  <span key={feature}><CheckDot />{feature}</span>
                ))}
              </div>
            </section>

            <div className={cx('page-plan-relation-grid')}>
              <section className={cx('page-plan-relation-block')}>
                <Text className={cx('page-plan-module-label')}><LinkOutlined /> 关联页面</Text>
                <div>
                  {page.relatedPageIds.length ? page.relatedPageIds.map((pageId) => (
                    <Tag className={cx('page-plan-relation-tag')} key={pageId}>
                      {pageNames.get(pageId) || pageId}
                    </Tag>
                  )) : <Text type="secondary">无直接页面跳转</Text>}
                </div>
              </section>
              <section className={cx('page-plan-relation-block')}>
                <Text className={cx('page-plan-module-label')}><ApiOutlined /> 关联 API</Text>
                <div>
                  {page.apiIds.length ? page.apiIds.map((apiId) => (
                    <Tag className={cx('page-plan-api-tag')} key={apiId}>{apiNames.get(apiId) || apiId}</Tag>
                  )) : <Text type="secondary">无需业务 API</Text>}
                </div>
              </section>
            </div>

            <section className={cx('page-plan-interaction-module')}>
              <div className={cx('page-plan-interaction-title')}>
                <Text className={cx('page-plan-module-label')}><ThunderboltOutlined /> 用户交互路径</Text>
                <Text type="secondary">按用户实际操作顺序</Text>
              </div>
              {page.interactions.length ? (
                <div className={cx('page-plan-interaction-list')}>
                  {page.interactions.map((interaction, interactionIndex) => (
                    <div className={cx('page-plan-interaction-step')} key={`${page.id}-${interaction.name}`}>
                      <span className={cx('page-plan-interaction-index')}>{interactionIndex + 1}</span>
                      <div>
                        <Text strong>{interaction.name}</Text>
                        <dl>
                          <div><dt>触发</dt><dd>{interaction.trigger}</dd></div>
                          <div><dt>用户</dt><dd>{interaction.userAction}</dd></div>
                          <div><dt>系统</dt><dd>{interaction.systemResponse}</dd></div>
                        </dl>
                        {interaction.targetPageId || interaction.apiIds.length ? (
                          <div className={cx('page-plan-interaction-result')}>
                            {interaction.targetPageId ? <span>前往 {pageNames.get(interaction.targetPageId) || interaction.targetPageId}</span> : null}
                            {interaction.apiIds.map((apiId) => <span key={apiId}>调用 {apiNames.get(apiId) || apiId}</span>)}
                          </div>
                        ) : null}
                      </div>
                    </div>
                  ))}
                </div>
              ) : <Paragraph type="secondary">该页面以浏览和本地状态交互为主。</Paragraph>}
            </section>
          </article>
        ))}
      </div>

      <header className={cx('page-plan-section-header', 'is-api-section')}>
        <span className={cx('page-plan-section-icon')}><ApiOutlined /></span>
        <div>
          <Title level={4}>API 功能设计</Title>
          <Paragraph type="secondary">描述页面所依赖的业务能力与数据契约，本阶段不生成实现代码。</Paragraph>
        </div>
        <span className={cx('page-plan-section-count')}>{plan.apis.length} 个 API</span>
      </header>

      {plan.apis.length ? (
        <div className={cx('page-plan-api-grid')}>
          {plan.apis.map((api) => (
            <article className={cx('page-plan-api-card')} key={api.id}>
              <header>
                <span className={cx('page-plan-method', `is-${api.method.toLowerCase()}`)}>{api.method}</span>
                <div><Text strong>{api.name}</Text><Text code>{api.path}</Text></div>
              </header>
              <Paragraph className={cx('page-plan-api-purpose')}>{api.purpose}</Paragraph>
              <dl className={cx('page-plan-api-contract')}>
                <div><dt>请求设计</dt><dd>{api.requestDesign}</dd></div>
                <div><dt>响应设计</dt><dd>{api.responseDesign}</dd></div>
              </dl>
              <footer>
                <Text type="secondary">使用页面</Text>
                <div>
                  {api.usedByPageIds.map((pageId) => (
                    <Tag className={cx('page-plan-relation-tag')} key={pageId}>
                      {pageNames.get(pageId) || pageId}
                    </Tag>
                  ))}
                </div>
              </footer>
            </article>
          ))}
        </div>
      ) : <Paragraph type="secondary">当前方案不需要后端业务 API，页面交互均可在前端完成。</Paragraph>}
    </section>
  )
}

// 渲染核心功能前的紫色状态点，保持功能列表轻量且易扫描。
function CheckDot(): JSX.Element {
  return <i aria-hidden className={cx('page-plan-check-dot')} />
}
