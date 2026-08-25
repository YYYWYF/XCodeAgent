import { useState } from 'react'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import {
  RequirementEntitiesSection,
  RequirementFlowsSection,
  RequirementOverviewSection,
  RequirementPagesSection,
  type RequirementSectionKey
} from './RequirementDocSections'
import type { JsonRecord } from './RequirementDocPanelData'
import './RequirementDocPanel.less'

type Props = {
  productPlan: JsonRecord
  spec: JsonRecord
}

const requirementDocSections: Array<{ key: RequirementSectionKey; label: string }> = [
  { key: 'overview', label: '概览' },
  { key: 'pages', label: '页面' },
  { key: 'entities', label: '实体' },
  { key: 'flows', label: '业务流程' }
]

/** 渲染右侧需求文档产物视图，所有交互仅改变本面板内的阅读位置。 */
export default function RequirementDocPanel({ productPlan, spec }: Props): ReactElement {
  const [activeSection, setActiveSection] = useState<RequirementSectionKey>('overview')
  return (
    <div className={cx('requirement-doc-panel')}>
      <div className={cx('requirement-doc-section-tabs')} role="tablist" aria-label="需求文档章节">
        {requirementDocSections.map((section) => (
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
      {activeSection === 'pages' ? (
        <RequirementPagesSection productPlan={productPlan} sectionKey="pages" spec={spec} />
      ) : null}
      {activeSection === 'entities' ? (
        <RequirementEntitiesSection sectionKey="entities" spec={spec} />
      ) : null}
      {activeSection === 'flows' ? (
        <RequirementFlowsSection sectionKey="flows" spec={spec} />
      ) : null}
    </div>
  )
}
