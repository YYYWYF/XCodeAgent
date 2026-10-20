import { BgColorsOutlined, InteractionOutlined, LayoutOutlined } from '@ant-design/icons'
import { Tag, Typography } from 'antd'
import { useState } from 'react'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import {
  ArchitectureSection,
  ContractSection,
  EntitiesSection,
  PageBindingsSection,
  type SectionKey
} from './TechnicalPlanDocSections'
import {
  requirementInteractionRows,
  requirementLayoutRows,
  requirementStyleHints
} from './RequirementDocPanelData'
import { AuthorizationSection } from './TechnicalPlanAuthorizationSection'
import { authorizationDesignView } from './TechnicalPlanAuthorizationData'
import { asRecord, recordItems, textValue, type JsonRecord } from './TechnicalPlanDocPanelData'
import './TechnicalPlanDocPanel.less'

type Props = {
  plan: JsonRecord
  productPlan: JsonRecord
  /** RequirementSpec：用于「页面风格」从自由文本按维度提取风格标签。 */
  spec?: JsonRecord
}

const technicalPlanSections: Array<{ key: SectionKey; label: string }> = [
  { key: 'architecture', label: '架构' },
  { key: 'entities', label: '实体' },
  { key: 'api-contracts', label: 'API 契约' },
  { key: 'page-bindings', label: '页面绑定' },
  // 页面概览：聚合页面交互/布局/风格三块，与需求文档面板概览 tab 视角一致。
  // 接口请求与响应不在此 tab，避免与「API 契约」tab 内容重复。
  { key: 'page-design-overview', label: '页面概览' }
]

/** 渲染右侧技术规划产物视图，所有交互仅改变本面板内的阅读位置。 */
export default function TechnicalPlanDocPanel({
  plan,
  productPlan,
  spec
}: Props): ReactElement {
  const architecture = asRecord(plan.architecture)
  const entities = recordItems(plan.entities)
  const contracts = recordItems(plan.api_contracts)
  const pages = recordItems(plan.pages)
  const hasAuthorization = Boolean(authorizationDesignView(plan))
  const sections = hasAuthorization
    ? [...technicalPlanSections, { key: 'authorization' as const, label: '权限' }]
    : technicalPlanSections
  const [activeSection, setActiveSection] = useState<SectionKey>('api-contracts')
  const resolvedActiveSection = sections.some((section) => section.key === activeSection)
    ? activeSection
    : 'api-contracts'
  const [selectedContractId, setSelectedContractId] = useState('')
  const [selectedEndpointId, setSelectedEndpointId] = useState('')
  const resolvedContract =
    contracts.find(
      (contract, index) => textValue(contract.id, `contract-${index + 1}`) === selectedContractId
    ) ||
    contracts[0] ||
    {}
  const resolvedContractId = textValue(resolvedContract.id, contracts.length ? 'contract-1' : '')
  const endpoints = recordItems(resolvedContract.endpoints)
  const resolvedEndpoint =
    endpoints.find(
      (endpoint, index) =>
        textValue(endpoint.id, `${resolvedContractId}-endpoint-${index + 1}`) === selectedEndpointId
    ) ||
    endpoints[0] ||
    {}
  const resolvedEndpointId = textValue(
    resolvedEndpoint.id,
    endpoints.length ? `${resolvedContractId}-endpoint-1` : ''
  )

  return (
    <div className={cx('technical-plan-doc-panel')}>
      <div className={cx('technical-plan-section-tabs')} role="tablist" aria-label="技术规划章节">
        {sections.map((section) => (
          <button
            aria-controls={`technical-plan-panel-${section.key}`}
            aria-selected={resolvedActiveSection === section.key}
            className={cx(
              'technical-plan-section-tab',
              resolvedActiveSection === section.key && 'is-active'
            )}
            key={section.key}
            id={`technical-plan-tab-${section.key}`}
            onClick={() => setActiveSection(section.key)}
            role="tab"
            type="button"
          >
            {section.label}
          </button>
        ))}
      </div>
      {resolvedActiveSection === 'architecture' ? (
        <ArchitectureSection architecture={architecture} sectionKey="architecture" />
      ) : null}
      {resolvedActiveSection === 'entities' ? (
        <EntitiesSection entities={entities} sectionKey="entities" />
      ) : null}
      {resolvedActiveSection === 'api-contracts' ? (
        <ContractSection
          contracts={contracts}
          onContractChange={(id) => {
            setSelectedContractId(id)
            setSelectedEndpointId('')
          }}
          onEndpointChange={setSelectedEndpointId}
          selectedContract={resolvedContract}
          selectedContractId={resolvedContractId}
          selectedEndpoint={resolvedEndpoint}
          selectedEndpointId={resolvedEndpointId}
          sectionKey="api-contracts"
        />
      ) : null}
      {resolvedActiveSection === 'page-bindings' ? (
        <PageBindingsSection
          contracts={contracts}
          pages={pages}
          productPlan={productPlan}
          sectionKey="page-bindings"
        />
      ) : null}
      {resolvedActiveSection === 'authorization' ? (
        <AuthorizationSection plan={plan} sectionKey="authorization" />
      ) : null}
      {resolvedActiveSection === 'page-design-overview' ? (
        <PageDesignOverviewSection
          productPlan={productPlan}
          spec={spec || {}}
          sectionKey="page-design-overview"
        />
      ) : null}
    </div>
  )
}

/**
 * 渲染「页面概览」tab：聚合页面交互 / 页面布局 / 页面风格三块，紧凑行展示。
 * 交互/布局来自 ProductPlan，风格来自 RequirementSpec 自由文本。每块用彩色标题条
 * + 行内标签，减少嵌套卡片层数，避免与需求文档面板的卡片网格重复视觉。
 * 接口请求与响应不在此 tab，避免与「API 契约」tab 内容重复。
 */
function PageDesignOverviewSection({
  productPlan,
  spec,
  sectionKey
}: {
  productPlan: JsonRecord
  spec: JsonRecord
  sectionKey: SectionKey
}): ReactElement {
  const { Text } = Typography
  // 三块拍平：交互/布局来自 productPlan，风格来自 spec 自由文本。
  const interactions = requirementInteractionRows(productPlan)
  const layouts = requirementLayoutRows(productPlan)
  const styleHints = requirementStyleHints(spec)
  return (
    <section
      aria-label="页面概览"
      className={cx('technical-plan-section', 'page-design-overview')}
      id={`technical-plan-panel-${sectionKey}`}
      role="tabpanel"
    >
      {/* 页面交互：彩色标题条 + 每页一张子卡，列出操作(行为类型+预期结果)与跳转目标。 */}
      <div className={cx('page-design-overview-block', 'is-interaction')}>
        <div className={cx('page-design-overview-bar')}>
          <InteractionOutlined />
          <span>页面交互</span>
        </div>
        <div className={cx('page-design-overview-cards')}>
          {interactions.length ? (
            interactions.map((interaction) => (
              <article className={cx('page-design-overview-card')} key={interaction.key}>
                <header className={cx('page-design-overview-card-title')}>
                  <Text strong>{interaction.pageName}</Text>
                </header>
                {interaction.actions.length ? (
                  <ul className={cx('page-design-overview-action-list')}>
                    {interaction.actions.map((action) => (
                      <li className={cx('page-design-overview-action')} key={action.key}>
                        <div className={cx('page-design-overview-action-head')}>
                          <Text>{action.name}</Text>
                          <em
                            className={cx(
                              'page-design-overview-action-type',
                              `is-${action.behaviorType || 'operation'}`
                            )}
                          >
                            {action.behaviorTypeLabel}
                          </em>
                        </div>
                        {action.expectedResult ? (
                          <Text className={cx('page-design-overview-action-result')} type="secondary">
                            {action.expectedResult}
                          </Text>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <Text type="secondary">该页面暂无操作</Text>
                )}
                {interaction.navigationTargets.length ? (
                  <div className={cx('page-design-overview-tags')}>
                    {interaction.navigationTargets.map((target) => (
                      <Tag color="purple" key={target}>
                        {target}
                      </Tag>
                    ))}
                  </div>
                ) : null}
              </article>
            ))
          ) : (
            <Text type="secondary">暂无页面交互</Text>
          )}
        </div>
      </div>
      {/* 页面布局：彩色标题条 + 每页一张子卡，列出页面目标与业务信息项(标签+描述)。 */}
      <div className={cx('page-design-overview-block', 'is-layout')}>
        <div className={cx('page-design-overview-bar')}>
          <LayoutOutlined />
          <span>页面布局</span>
        </div>
        <div className={cx('page-design-overview-cards')}>
          {layouts.length ? (
            layouts.map((layout) => (
              <article className={cx('page-design-overview-card')} key={layout.key}>
                <header className={cx('page-design-overview-card-title')}>
                  <Text strong>{layout.pageName}</Text>
                </header>
                {layout.goal ? (
                  <Text className={cx('page-design-overview-goal')} type="secondary">
                    {layout.goal}
                  </Text>
                ) : null}
                {layout.informationItems.length ? (
                  <div className={cx('page-design-overview-tiles')}>
                    {layout.informationItems.map((item) => (
                      <div className={cx('page-design-overview-tile')} key={item.key}>
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
      </div>
      {/* 页面风格：彩色标题条 + 每维度一张子卡，列出风格标签与来源原文。 */}
      <div className={cx('page-design-overview-block', 'is-style')}>
        <div className={cx('page-design-overview-bar')}>
          <BgColorsOutlined />
          <span>页面风格</span>
        </div>
        <div className={cx('page-design-overview-cards')}>
          {styleHints.length ? (
            styleHints.map((hint) => (
              <article className={cx('page-design-overview-card')} key={hint.key}>
                <header className={cx('page-design-overview-card-title')}>
                  <Text strong>{hint.dimensionLabel}</Text>
                </header>
                <div className={cx('page-design-overview-tags')}>
                  {hint.labels.map((label) => (
                    <Tag color="blue" key={label}>
                      {label}
                    </Tag>
                  ))}
                </div>
                {hint.source ? (
                  <Text className={cx('page-design-overview-source')} type="secondary">
                    {hint.source}
                  </Text>
                ) : null}
              </article>
            ))
          ) : (
            <Text type="secondary">暂无页面风格</Text>
          )}
        </div>
      </div>
    </section>
  )
}
