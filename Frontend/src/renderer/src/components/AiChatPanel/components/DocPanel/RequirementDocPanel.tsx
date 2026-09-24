import { useState } from 'react'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import { RequirementAgentSection } from './RequirementAgentSection'
import {
  RequirementApiContractsSection,
  RequirementEntitiesSection,
  RequirementFlowsSection,
  RequirementOverviewSection,
  RequirementPagesSection,
  type RequirementSectionKey
} from './RequirementDocSections'
import { recordItems, requirementAgentRows, type JsonRecord } from './RequirementDocPanelData'
import './RequirementDocPanel.less'

type Props = {
  agentSurfaceSelectionEditable?: boolean
  onAgentSurfaceEnabledChange?: (
    agentId: string,
    pageId: string,
    enabled: boolean
  ) => Promise<void>
  productPlan: JsonRecord
  spec: JsonRecord
  /** 只读引用的技术规划，仅用于展示已确认的 API 契约；设计阶段不生成技术规划事实。 */
  technicalPlan?: JsonRecord
}

/** 渲染右侧需求文档产物视图，所有交互仅改变本面板内的阅读位置。 */
export default function RequirementDocPanel({
  agentSurfaceSelectionEditable,
  onAgentSurfaceEnabledChange,
  productPlan,
  spec,
  technicalPlan
}: Props): ReactElement {
  const [activeSection, setActiveSection] = useState<RequirementSectionKey>('overview')
  const hasAgents = requirementAgentRows(productPlan, spec).length > 0
  const hasApiContracts = Boolean(
    technicalPlan && recordItems(technicalPlan.api_contracts).length > 0
  )
  const requirementDocSections: Array<{ key: RequirementSectionKey; label: string }> = [
    { key: 'overview', label: '概览' },
    ...(hasAgents ? [{ key: 'agents' as const, label: '智能体' }] : []),
    { key: 'pages', label: '页面' },
    { key: 'entities', label: '实体' },
    ...(hasApiContracts ? [{ key: 'api-contracts' as const, label: 'API 契约' }] : []),
    { key: 'flows', label: '业务流程' }
  ]
  // 当前选中的章节可能因上游数据变化被移除（如智能体或 API 契约消失），回落概览。
  const visibleSection = requirementDocSections.some((section) => section.key === activeSection)
    ? activeSection
    : 'overview'
  return (
    <div className={cx('requirement-doc-panel')}>
      <div className={cx('requirement-doc-section-tabs')} role="tablist" aria-label="需求文档章节">
        {requirementDocSections.map((section) => (
          <button
            aria-controls={`requirement-doc-panel-${section.key}`}
            aria-selected={visibleSection === section.key}
            className={cx(
              'requirement-doc-section-tab',
              visibleSection === section.key && 'is-active'
            )}
            key={section.key}
            id={`requirement-doc-tab-${section.key}`}
            onClick={() => setActiveSection(section.key)}
            role="tab"
            type="button"
          >
            {section.label}
          </button>
        ))}
      </div>
      {visibleSection === 'overview' ? (
        <RequirementOverviewSection sectionKey="overview" spec={spec} />
      ) : null}
      {visibleSection === 'agents' ? (
        <RequirementAgentSection
          editable={agentSurfaceSelectionEditable}
          onSurfaceEnabledChange={onAgentSurfaceEnabledChange}
          productPlan={productPlan}
          spec={spec}
        />
      ) : null}
      {visibleSection === 'pages' ? (
        <RequirementPagesSection productPlan={productPlan} sectionKey="pages" spec={spec} />
      ) : null}
      {visibleSection === 'entities' ? (
        <RequirementEntitiesSection sectionKey="entities" spec={spec} />
      ) : null}
      {visibleSection === 'api-contracts' && hasApiContracts ? (
        <RequirementApiContractsSection
          sectionKey="api-contracts"
          technicalPlan={technicalPlan || {}}
        />
      ) : null}
      {visibleSection === 'flows' ? (
        <RequirementFlowsSection sectionKey="flows" spec={spec} />
      ) : null}
    </div>
  )
}
