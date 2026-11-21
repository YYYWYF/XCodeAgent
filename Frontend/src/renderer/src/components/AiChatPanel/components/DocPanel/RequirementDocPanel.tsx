import { useState } from 'react'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import PageDesignOverviewSection from './PageDesignOverviewSection'
import RequirementAuthorizationSection from './RequirementAuthorizationSection'
import {
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
  /** 跨迭代增量合并产物：带 `_change_status` 标记的"展示版"RequirementSpec。
      后端 requirements 节点在 LLM 输出后与基线合并时同时写入。仅在"基于历史迭代"
      场景有值；为空时按"全部是本轮新增"展示。pages 子 tab 用它做
      "本次变更 vs 未变更内容"分区显示。 */
  specDiff?: JsonRecord
  workspaceRoot?: string
}

const requirementDocSections: Array<{ key: RequirementSectionKey; label: string }> = [
  { key: 'overview', label: '概览' },
  { key: 'pages', label: '页面' },
  { key: 'flows', label: '业务流程' },
  // 页面概览：从技术规划面板迁来（2026-10-21），数据全部来自上游 ProductPlan/RequirementSpec，
  // 与技术规划无关。放在设计阶段需求文档面板，让用户在确认需求时就能看到页面交互/布局/风格。
  { key: 'page-design-overview', label: '页面概览' },
  { key: 'authorization', label: '角色与权限' }
]

/** 渲染右侧需求文档产物视图，所有交互仅改变本面板内的阅读位置。 */
export default function RequirementDocPanel({
  productPlan,
  spec,
  specDiff,
  workspaceRoot
}: Props): ReactElement {
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
        <RequirementOverviewSection sectionKey="overview" spec={spec} specDiff={specDiff} />
      ) : null}
      {activeSection === 'pages' ? (
        <RequirementPagesSection productPlan={productPlan} sectionKey="pages" spec={spec} specDiff={specDiff} />
      ) : null}
      {activeSection === 'authorization' ? (
        <RequirementAuthorizationSection spec={spec} productPlan={productPlan} workspaceRoot={workspaceRoot} specDiff={specDiff} />
      ) : null}
      {activeSection === 'flows' ? (
        <RequirementFlowsSection sectionKey="flows" spec={spec} specDiff={specDiff} />
      ) : null}
      {activeSection === 'page-design-overview' ? (
        <PageDesignOverviewSection
          productPlan={productPlan}
          sectionKey="page-design-overview"
          spec={spec}
          specDiff={specDiff}
        />
      ) : null}
    </div>
  )
}
