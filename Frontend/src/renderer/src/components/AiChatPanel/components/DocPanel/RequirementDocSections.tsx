import {
  ApartmentOutlined,
  ArrowRightOutlined,
  BranchesOutlined,
  FileTextOutlined,
  TeamOutlined
} from '@ant-design/icons'
import { Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import {
  asRecord,
  behaviorTypeLabel,
  modulePriorityLabel,
  recordItems,
  requirementFlowRows,
  requirementPageRows,
  textValue,
  type JsonRecord,
  type RequirementPageRow
} from './RequirementDocPanelData'

const { Text } = Typography

export type RequirementSectionKey = 'overview' | 'agents' | 'pages' | 'flows'

type SectionProps = {
  sectionKey: RequirementSectionKey
}

/** 渲染应用摘要、用户角色与功能模块的总览，帮助用户先建立整体认识。 */
export function RequirementOverviewSection({
  spec,
  sectionKey
}: { spec: JsonRecord } & SectionProps): ReactElement {
  const appInfo = asRecord(spec.app_info)
  const roles = recordItems(spec.user_roles)
  const modules = recordItems(spec.feature_modules)
  return (
    <section
      aria-label="概览"
      className={cx('requirement-doc-section')}
      id={`requirement-doc-panel-${sectionKey}`}
      role="tabpanel"
    >
      <div className={cx('requirement-doc-section-title')}>
        <FileTextOutlined /> <span>应用概览</span>
      </div>
      <article className={cx('requirement-doc-app-card')}>
        <strong>{textValue(appInfo.name, '未命名应用')}</strong>
        {textValue(appInfo.summary) ? (
          <Text type="secondary">{textValue(appInfo.summary)}</Text>
        ) : null}
        <div className={cx('requirement-doc-app-meta')}>
          {textValue(appInfo.target_platform) ? (
            <Tag>{textValue(appInfo.target_platform)}</Tag>
          ) : null}
          {textValue(appInfo.navigation_layout) ? (
            <Tag>{textValue(appInfo.navigation_layout)}</Tag>
          ) : null}
        </div>
      </article>
      <div className={cx('requirement-doc-section-title')}>
        <TeamOutlined /> <span>用户角色</span>
      </div>
      <div className={cx('requirement-doc-card-list')}>
        {roles.length ? (
          roles.map((role, index) => (
            <article
              className={cx('requirement-doc-card')}
              key={textValue(role.id, `role-${index}`)}
            >
              <div className={cx('requirement-doc-card-heading')}>
                <strong>{textValue(role.name, `角色 ${index + 1}`)}</strong>
                <code>{textValue(role.id)}</code>
              </div>
              {textValue(role.description) ? (
                <Text type="secondary">{textValue(role.description)}</Text>
              ) : null}
            </article>
          ))
        ) : (
          <Text type="secondary">暂无用户角色</Text>
        )}
      </div>
      <div className={cx('requirement-doc-section-title')}>
        <ApartmentOutlined /> <span>功能模块</span>
      </div>
      <div className={cx('requirement-doc-card-list')}>
        {modules.length ? (
          modules.map((module, index) => (
            <article
              className={cx('requirement-doc-card')}
              key={textValue(module.id, `module-${index}`)}
            >
              <div className={cx('requirement-doc-card-heading')}>
                <strong>{textValue(module.name, `模块 ${index + 1}`)}</strong>
                <em
                  className={cx(
                    'requirement-doc-priority',
                    `is-${textValue(module.priority).toLowerCase() || 'must'}`
                  )}
                >
                  {modulePriorityLabel(module.priority)}
                </em>
              </div>
              {textValue(module.description) ? (
                <Text type="secondary">{textValue(module.description)}</Text>
              ) : null}
            </article>
          ))
        ) : (
          <Text type="secondary">暂无功能模块</Text>
        )}
      </div>
    </section>
  )
}

function PageCard({ page }: { page: RequirementPageRow }): ReactElement {
  return (
    <article className={cx('requirement-doc-page-card')} key={page.key}>
      <div className={cx('requirement-doc-page-heading')}>
        <div>
          <strong>{page.name}</strong>
          <code>{page.path || page.pageId}</code>
        </div>
        {page.moduleId ? <span>{page.moduleId}</span> : null}
      </div>
      {page.description ? <Text type="secondary">{page.description}</Text> : null}
      {page.goal ? (
        <p className={cx('requirement-doc-page-goal')}>
          <span>页面目标</span>
          {page.goal}
        </p>
      ) : null}
      {page.informationItems.length ? (
        <div className={cx('requirement-doc-page-block')}>
          <span className={cx('requirement-doc-page-block-title')}>业务信息</span>
          <ul>
            {page.informationItems.map((item) => (
              <li key={item.key}>
                <strong>{item.label}</strong>
                {item.description ? <span>{item.description}</span> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {page.actions.length ? (
        <div className={cx('requirement-doc-page-block')}>
          <span className={cx('requirement-doc-page-block-title')}>核心操作</span>
          <div className={cx('requirement-doc-action-list')}>
            {page.actions.map((action) => (
              <div className={cx('requirement-doc-action')} key={action.key}>
                <div className={cx('requirement-doc-action-heading')}>
                  <strong>{action.name}</strong>
                  <em className={cx(`is-${action.behaviorType}`)}>
                    {behaviorTypeLabel(action.behaviorType)}
                  </em>
                </div>
                {action.description ? <Text type="secondary">{action.description}</Text> : null}
                {action.expectedResult ? (
                  <div className={cx('requirement-doc-action-result')}>
                    <ArrowRightOutlined aria-hidden="true" />
                    <span>{action.expectedResult}</span>
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}
      {page.navigationTargets.length ? (
        <div className={cx('requirement-doc-page-block')}>
          <span className={cx('requirement-doc-page-block-title')}>页面跳转</span>
          <div className={cx('requirement-doc-tag-row')}>
            {page.navigationTargets.map((target) => (
              <Tag key={target}>{target}</Tag>
            ))}
          </div>
        </div>
      ) : null}
      {page.stateRequirements.length ? (
        <div className={cx('requirement-doc-page-block')}>
          <span className={cx('requirement-doc-page-block-title')}>状态要求</span>
          <ul>
            {page.stateRequirements.map((state) => (
              <li key={state.key}>
                <strong className={cx('requirement-doc-state-label', `is-${state.key}`)}>
                  {state.label}
                </strong>
                {state.description ? <span>{state.description}</span> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {page.acceptanceCriteria.length ? (
        <div className={cx('requirement-doc-page-block')}>
          <span className={cx('requirement-doc-page-block-title')}>产品验收标准</span>
          <ul className={cx('requirement-doc-acceptance')}>
            {page.acceptanceCriteria.map((criterion, index) => (
              <li key={`${page.key}-ac-${index}`}>{criterion}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </article>
  )
}

/** 渲染产品规划页面卡片：目标、业务信息、操作、跳转、状态与验收标准。 */
export function RequirementPagesSection({
  productPlan,
  spec,
  sectionKey
}: { productPlan: JsonRecord; spec: JsonRecord } & SectionProps): ReactElement {
  const pages = requirementPageRows(productPlan, recordItems(spec.pages))
  return (
    <section
      aria-label="页面"
      className={cx('requirement-doc-section')}
      id={`requirement-doc-panel-${sectionKey}`}
      role="tabpanel"
    >
      <div className={cx('requirement-doc-section-title', 'is-pages')}>
        <FileTextOutlined /> <span>页面与用户操作</span>
        <span className={cx('requirement-doc-section-count')}>{pages.length}</span>
      </div>
      <div className={cx('requirement-doc-page-list')}>
        {pages.length ? (
          pages.map((page) => <PageCard key={page.key} page={page} />)
        ) : (
          <Text type="secondary">暂无页面规划</Text>
        )}
      </div>
    </section>
  )
}

/** 渲染业务流程步骤，帮助用户理解跨页面的业务串联。 */
export function RequirementFlowsSection({
  spec,
  sectionKey
}: { spec: JsonRecord } & SectionProps): ReactElement {
  const flows = requirementFlowRows(spec)
  return (
    <section
      aria-label="业务流程"
      className={cx('requirement-doc-section')}
      id={`requirement-doc-panel-${sectionKey}`}
      role="tabpanel"
    >
      <div className={cx('requirement-doc-section-title', 'is-flows')}>
        <BranchesOutlined /> <span>业务流程</span>
        <span className={cx('requirement-doc-section-count')}>{flows.length}</span>
      </div>
      <div className={cx('requirement-doc-card-list')}>
        {flows.length ? (
          flows.map((flow) => (
            <article className={cx('requirement-doc-card')} key={flow.key}>
              <div className={cx('requirement-doc-card-heading')}>
                <strong>{flow.name}</strong>
              </div>
              {flow.description ? <Text type="secondary">{flow.description}</Text> : null}
              {flow.steps.length ? (
                <ol className={cx('requirement-doc-flow-steps')}>
                  {flow.steps.map((step, index) => (
                    <li key={`${flow.key}-step-${index}`}>{step}</li>
                  ))}
                </ol>
              ) : null}
            </article>
          ))
        ) : (
          <Text type="secondary">暂无业务流程</Text>
        )}
      </div>
    </section>
  )
}
