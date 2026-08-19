import { useState } from 'react'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import { RequirementAgentSection } from './RequirementAgentSection'
import {
  RequirementFlowsSection,
  RequirementOverviewSection,
  RequirementPagesSection,
  type RequirementSectionKey
} from './RequirementDocSections'
import { requirementAgentRows, type JsonRecord } from './RequirementDocPanelData'
import './RequirementDocPanel.less'

type Props = {
  productPlan: JsonRecord
  spec: JsonRecord
}

const baseRequirementDocSections: Array<{ key: RequirementSectionKey; label: string }> = [
  { key: 'overview', label: '概览' },
  { key: 'pages', label: '页面' },
  { key: 'flows', label: '业务流程' }
]

/** 渲染右侧需求文档产物视图，所有交互仅改变本面板内的阅读位置。 */
export default function RequirementDocPanel({ productPlan, spec }: Props): ReactElement {
  const [activeSection, setActiveSection] = useState<RequirementSectionKey>('overview')
  const hasAgents = requirementAgentRows(productPlan, spec).length > 0
  const visibleSection = !hasAgents && activeSection === 'agents' ? 'overview' : activeSection
  const requirementDocSections = hasAgents
    ? [
        baseRequirementDocSections[0],
        { key: 'agents' as const, label: '智能体' },
        ...baseRequirementDocSections.slice(1)
      ]
    : baseRequirementDocSections
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
        <RequirementAgentSection productPlan={productPlan} spec={spec} />
      ) : null}
      {visibleSection === 'pages' ? (
        <RequirementPagesSection productPlan={productPlan} sectionKey="pages" spec={spec} />
      ) : null}
      {visibleSection === 'flows' ? (
        <RequirementFlowsSection sectionKey="flows" spec={spec} />
      ) : null}
    </div>
  )
}
