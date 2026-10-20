import {
  ApartmentOutlined,
  ApiOutlined,
  ArrowRightOutlined,
  BgColorsOutlined,
  BranchesOutlined,
  FileTextOutlined,
  InteractionOutlined,
  LayoutOutlined,
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
  requirementApiContractRows,
  requirementFlowRows,
  requirementInteractionRows,
  requirementLayoutRows,
  requirementPageRows,
  requirementStyleHints,
  stringItems,
  textValue,
  type JsonRecord,
  type RequirementApiContractRow,
  type RequirementInteractionRow,
  type RequirementLayoutRow,
  type RequirementPageRow,
  type RequirementStyleHintRow
} from './RequirementDocPanelData'

const { Text } = Typography

export type RequirementSectionKey = 'overview' | 'pages' | 'flows'

type SectionProps = {
  sectionKey: RequirementSectionKey
}

/** 渲染应用摘要、用户角色与功能模块的总览，帮助用户先建立整体认识。 */
export function RequirementOverviewSection({
  spec,
  productPlan,
  technicalPlan,
  sectionKey
}: {
  spec: JsonRecord
  productPlan: JsonRecord
  technicalPlan: JsonRecord
} & SectionProps): ReactElement {
  const appInfo = asRecord(spec.app_info)
  const roles = recordItems(spec.user_roles)
  const modules = recordItems(spec.feature_modules)
  // 概览扩展四块：从 spec + 已有后续产物合并读，缺失时显示空态文案。
  const interactions = requirementInteractionRows(productPlan)
  const layouts = requirementLayoutRows(productPlan)
  const styleHints = requirementStyleHints(spec)
  const apiContracts = requirementApiContractRows(technicalPlan)
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
            <article className={cx('requirement-doc-card')} key={textValue(role.id, `role-${index}`)}>
              <div className={cx('requirement-doc-card-heading')}>
                <strong>{textValue(role.name, `角色 ${index + 1}`)}</strong>
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
      {/* 概览扩展四块：页面交互 / 页面布局 / 页面风格 / 接口请求与响应。
          各自抽成独立 section 组件，便于产品/技术规划面板按数据来源单独复用。 */}
      <RequirementInteractionSection rows={interactions} />
      <RequirementLayoutSection rows={layouts} />
      <RequirementStyleSection rows={styleHints} />
      <RequirementApiContractSection rows={apiContracts} />
    </section>
  )
}

/** 渲染「页面交互」区块：按页面聚合 ProductPlan 各页的操作与跳转目标。 */
export function RequirementInteractionSection({
  rows
}: {
  rows: RequirementInteractionRow[]
}): ReactElement {
  return (
    <>
      <div className={cx('requirement-doc-section-title')}>
        <InteractionOutlined /> <span>页面交互</span>
      </div>
      <div className={cx('requirement-doc-card-list')}>
        {rows.length ? (
          rows.map((interaction) => (
            <article className={cx('requirement-doc-card')} key={interaction.key}>
              <div className={cx('requirement-doc-card-heading')}>
                <strong>{interaction.pageName}</strong>
              </div>
              {interaction.actions.length ? (
                <div className={cx('requirement-doc-action-list')}>
                  {interaction.actions.map((action) => (
                    <div className={cx('requirement-doc-action')} key={action.key}>
                      <div className={cx('requirement-doc-action-heading')}>
                        <strong>{action.name}</strong>
                        <em className={cx(`is-${action.behaviorType}`)}>
                          {action.behaviorTypeLabel}
                        </em>
                      </div>
                      {action.expectedResult ? (
                        <div className={cx('requirement-doc-action-result')}>
                          <ArrowRightOutlined aria-hidden="true" />
                          <span>{action.expectedResult}</span>
                        </div>
                      ) : null}
                    </div>
                  ))}
                </div>
              ) : (
                <Text type="secondary">该页面暂无操作</Text>
              )}
              {interaction.navigationTargets.length ? (
                <div className={cx('requirement-doc-tag-row')}>
                  {interaction.navigationTargets.map((target) => (
                    <Tag key={target}>{target}</Tag>
                  ))}
                </div>
              ) : null}
            </article>
          ))
        ) : (
          <Text type="secondary">暂无页面交互</Text>
        )}
      </div>
    </>
  )
}

/** 渲染「页面布局」区块：按页面展示目标与业务信息项。 */
export function RequirementLayoutSection({
  rows
}: {
  rows: RequirementLayoutRow[]
}): ReactElement {
  return (
    <>
      <div className={cx('requirement-doc-section-title')}>
        <LayoutOutlined /> <span>页面布局</span>
      </div>
      <div className={cx('requirement-doc-card-list')}>
        {rows.length ? (
          rows.map((layout) => (
            <article className={cx('requirement-doc-card')} key={layout.key}>
              <div className={cx('requirement-doc-card-heading')}>
                <strong>{layout.pageName}</strong>
              </div>
              {layout.goal ? <Text type="secondary">{layout.goal}</Text> : null}
              {layout.informationItems.length ? (
                <div className={cx('requirement-doc-page-tiles')}>
                  {layout.informationItems.map((item) => (
                    <div className={cx('requirement-doc-page-tile')} key={item.key}>
                      <strong>{item.label}</strong>
                      {item.description ? <span>{item.description}</span> : null}
                    </div>
                  ))}
                </div>
              ) : (
                <Text type="secondary">该页面暂无业务信息</Text>
              )}
            </article>
          ))
        ) : (
          <Text type="secondary">暂无页面布局</Text>
        )}
      </div>
    </>
  )
}

/** 渲染「页面风格」区块：从 spec 自由文本按维度提取的风格标签卡。 */
export function RequirementStyleSection({
  rows
}: {
  rows: RequirementStyleHintRow[]
}): ReactElement {
  return (
    <>
      <div className={cx('requirement-doc-section-title')}>
        <BgColorsOutlined /> <span>页面风格</span>
      </div>
      <div className={cx('requirement-doc-card-list')}>
        {rows.length ? (
          rows.map((hint) => (
            <article className={cx('requirement-doc-card')} key={hint.key}>
              <div className={cx('requirement-doc-card-heading')}>
                <strong>{hint.dimensionLabel}</strong>
              </div>
              <div className={cx('requirement-doc-tag-row')}>
                {hint.labels.map((label) => (
                  <Tag key={label}>{label}</Tag>
                ))}
              </div>
              {hint.source ? <Text type="secondary">{hint.source}</Text> : null}
            </article>
          ))
        ) : (
          <Text type="secondary">暂无页面风格</Text>
        )}
      </div>
    </>
  )
}

/** 渲染「接口请求与响应」区块：遍历 TechnicalPlan.api_contracts 拍平的摘要卡。 */
export function RequirementApiContractSection({
  rows
}: {
  rows: RequirementApiContractRow[]
}): ReactElement {
  return (
    <>
      <div className={cx('requirement-doc-section-title')}>
        <ApiOutlined /> <span>接口请求与响应</span>
      </div>
      <div className={cx('requirement-doc-card-list')}>
        {rows.length ? (
          rows.map((contract) => (
            <article className={cx('requirement-doc-card')} key={contract.key}>
              <div className={cx('requirement-doc-card-heading')}>
                <strong>{contract.method} {contract.path}</strong>
              </div>
              {contract.summary ? <Text type="secondary">{contract.summary}</Text> : null}
              <div className={cx('requirement-doc-app-meta')}>
                <Tag>请求 {contract.requestSchemaRef || '无'}</Tag>
                <Tag>响应 {contract.responseSchemaRef || '无'}</Tag>
              </div>
              {contract.parameters && contract.parameters !== '无' ? (
                <Text type="secondary">参数：{contract.parameters}</Text>
              ) : null}
            </article>
          ))
        ) : (
          <Text type="secondary">暂无接口请求与响应</Text>
        )}
      </div>
    </>
  )
}

/** 渲染单张页面卡：头部（序号 + 名称 + 路径）、主区（目标 / 业务信息 / 操作）与规则区。 */
function PageCard({ page, index }: { page: RequirementPageRow; index: number }): ReactElement {
  return (
    <article className={cx('requirement-doc-page-card')} key={page.key}>
      <header className={cx('requirement-doc-page-head')}>
        <span className={cx('requirement-doc-page-index')} aria-hidden="true">
          {index + 1}
        </span>
        <div className={cx('requirement-doc-page-title')}>
          <strong>{page.name}</strong>
          {page.path || page.pageId ? (
            <code className={cx('requirement-doc-page-path')}>{page.path || page.pageId}</code>
          ) : null}
        </div>
      </header>
      {page.description ? (
        <p className={cx('requirement-doc-page-desc')}>{page.description}</p>
      ) : null}

      {/* 主区：每块套一个浅底内嵌面板，让层次从"平铺的小标题"变成"可分辨的区块"。 */}
      {page.goal ? (
        <section className={cx('requirement-doc-page-zone')}>
          <span className={cx('requirement-doc-page-zone-title')}>页面目标</span>
          <p className={cx('requirement-doc-page-zone-body')}>{page.goal}</p>
        </section>
      ) : null}

      {page.informationItems.length ? (
        <section className={cx('requirement-doc-page-zone')}>
          <span className={cx('requirement-doc-page-zone-title')}>业务信息</span>
          {/* 磁贴网格而非列表：信息项之间彼此独立，网格比竖排列表更易扫读。 */}
          <div className={cx('requirement-doc-page-tiles')}>
            {page.informationItems.map((item) => (
              <div className={cx('requirement-doc-page-tile')} key={item.key}>
                <strong>{item.label}</strong>
                {item.description ? <span>{item.description}</span> : null}
              </div>
            ))}
          </div>
        </section>
      ) : null}

      {page.actions.length ? (
        <section className={cx('requirement-doc-page-zone')}>
          <span className={cx('requirement-doc-page-zone-title')}>核心操作</span>
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
        </section>
      ) : null}

      {page.navigationTargets.length ? (
        <section className={cx('requirement-doc-page-zone')}>
          <span className={cx('requirement-doc-page-zone-title')}>页面跳转</span>
          <div className={cx('requirement-doc-tag-row')}>
            {page.navigationTargets.map((target) => (
              <Tag key={target}>{target}</Tag>
            ))}
          </div>
        </section>
      ) : null}

      {/* 规则区：状态要求与验收标准是长尾细节，合并成一个弱化区（分隔线而非面板底），
          不与目标 / 操作抢注意力，但内容一条不少、不折叠。 */}
      {page.stateRequirements.length || page.acceptanceCriteria.length ? (
        <section className={cx('requirement-doc-page-zone', 'is-muted')}>
          {page.stateRequirements.length ? (
            <>
              <span className={cx('requirement-doc-page-zone-title')}>状态要求</span>
              <ul className={cx('requirement-doc-page-rules')}>
                {page.stateRequirements.map((state) => (
                  <li key={state.key}>
                    <strong className={cx('requirement-doc-state-label', `is-${state.key}`)}>
                      {state.label}
                    </strong>
                    {state.description ? <span>{state.description}</span> : null}
                  </li>
                ))}
              </ul>
            </>
          ) : null}
          {page.acceptanceCriteria.length ? (
            <>
              <span className={cx('requirement-doc-page-zone-title')}>产品验收标准</span>
              <ul className={cx('requirement-doc-page-rules', 'requirement-doc-acceptance')}>
                {page.acceptanceCriteria.map((criterion, criterionIndex) => (
                  <li key={`${page.key}-ac-${criterionIndex}`}>{criterion}</li>
                ))}
              </ul>
            </>
          ) : null}
        </section>
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
          pages.map((page, index) => <PageCard index={index} key={page.key} page={page} />)
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

export { stringItems }
