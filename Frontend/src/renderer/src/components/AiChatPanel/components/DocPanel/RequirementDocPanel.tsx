import { useState } from 'react'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import PageDesignOverviewSection from './PageDesignOverviewSection'
import {
  RequirementFlowsSection,
  RequirementOverviewSection,
  RequirementPagesSection,
  type RequirementSectionKey
} from './RequirementDocSections'
import type { JsonRecord } from './RequirementDocPanelData'
import './RequirementDocPanel.less'
import RequirementAgentSection from './RequirementAgentSection'

type Props = {
  workspaceRoot?: string
  onChanged?: () => void
  productPlan: JsonRecord
  spec: JsonRecord
}

const requirementDocSections: Array<{ key: RequirementSectionKey; label: string }> = [
  { key: 'overview', label: '概览' },
  { key: 'pages', label: '页面' },
  { key: 'flows', label: '业务流程' },
  // 页面概览：从技术规划面板迁来（2026-10-21），数据全部来自上游 ProductPlan/RequirementSpec，
  // 与技术规划无关。放在设计阶段需求文档面板，让用户在确认需求时就能看到页面交互/布局/风格。
  { key: 'page-design-overview', label: '页面概览' }
]

/** 渲染右侧需求文档产物视图，所有交互仅改变本面板内的阅读位置。 */
export default function RequirementDocPanel({
  workspaceRoot,
  onChanged,
  productPlan,
  spec
}: Props): ReactElement {
  const [activeSection, setActiveSection] = useState<RequirementSectionKey>('overview')
  const hasAgents = (Array.isArray(productPlan.agents) && productPlan.agents.length > 0) || (Array.isArray(spec.agent_requirements) && spec.agent_requirements.length > 0)
  const sections = hasAgents ? [...requirementDocSections, { key: 'agents' as const, label: '智能体' }] : requirementDocSections
  return (
    <div className={cx('requirement-doc-panel')}>
      <div className={cx('requirement-doc-section-tabs')} role="tablist" aria-label="需求文档章节">
        {sections.map((section) => (
          <button
            aria-controls={`requirement-doc-panel-${section.key}`}
            aria-selected={activeSection === section.key}
            className={cx(
              'requirement-doc-section-tab',
              activeSection === section.key && 'is-active'
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
      {activeSection === 'overview' ? (
        <RequirementOverviewSection sectionKey="overview" spec={spec} />
      ) : null}
      {activeSection === 'agents' && hasAgents ? <RequirementAgentSection spec={spec} productPlan={productPlan} workspaceRoot={workspaceRoot} onChanged={onChanged} /> : null}
      {activeSection === 'pages' ? (
        <RequirementPagesSection productPlan={productPlan} sectionKey="pages" spec={spec} />
      ) : null}
      {activeSection === 'flows' ? (
        <RequirementFlowsSection sectionKey="flows" spec={spec} />
      ) : null}
      {activeSection === 'page-design-overview' ? (
        <PageDesignOverviewSection
          productPlan={productPlan}
          sectionKey="page-design-overview"
          spec={spec}
        />
      ) : null}
    </div>
  )
}
