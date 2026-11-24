import { BgColorsOutlined, InteractionOutlined, LayoutOutlined } from '@ant-design/icons'
import { Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import {
  requirementInteractionRows,
  recordItems,
  requirementLayoutRows,
  requirementStyleHints,
  type RequirementStyleHintRow
} from './RequirementDocPanelData'
import { ChangeBadge, type ChangeStatus } from './RequirementDocSections'
import type { JsonRecord } from './TechnicalPlanDocPanelData'
import './TechnicalPlanDocPanel.less'

const { Text } = Typography

type Props = {
  productPlan: JsonRecord
  spec: JsonRecord
  /** 上一轮需求草稿；存在时按各风格维度的实际标签比较，而不是比较整段应用摘要。 */
  specBaseline?: JsonRecord
  /** 跨迭代增量合并产物（带 `_change_status`）：按 pageId 提供页面的本轮变更类型；
      `_page_design_overview` 进一步提供交互/布局子区域状态。为空表示首轮创建，按"本轮新增"展示。 */
  specDiff?: JsonRecord
  /** 触发 aria id 与 tab 对齐的 section key（如 'page-design-overview'）。 */
  sectionKey: string
}

/**
 * 解析应用级变更徽标：读后端写在 diff 上的 `_app_change_status`。
 * 没有 diff（首轮创建）时全部按"新增"；有 diff 但缺该标记时按"未变更"兜底，
 * 避免旧数据把风格维度误标成新增。
 */
function resolveAppStatus(specDiff: JsonRecord | undefined): ChangeStatus {
  if (!specDiff) return 'added'
  const status = specDiff._app_change_status
  if (status === 'added' || status === 'modified' || status === 'unchanged' || status === 'deleted') {
    return status
  }
  return 'unchanged'
}

/** 比较同一风格维度的实际标签；来源句子随业务摘要变化不代表视觉风格发生变化。 */
function resolveStyleStatus(
  hint: RequirementStyleHintRow,
  baselineHints: RequirementStyleHintRow[] | undefined,
  fallback: ChangeStatus
): ChangeStatus {
  if (!baselineHints) return fallback
  const previous = baselineHints.find((item) => item.dimension === hint.dimension)
  if (!previous) return 'added'
  const before = [...previous.labels].sort()
  const after = [...hint.labels].sort()
  return JSON.stringify(before) === JSON.stringify(after) ? 'unchanged' : 'modified'
}

/** 从 specDiff 的页面记录里按 pageId 取出本轮变更类型，供页面卡片打标。 */
function pageChangeStatusMap(specDiff: JsonRecord | undefined): Map<string, ChangeStatus> {
  const map = new Map<string, ChangeStatus>()
  const pages = Array.isArray(specDiff?.pages) ? (specDiff?.pages as unknown[]) : []
  pages.forEach((item, index) => {
    if (!item || typeof item !== 'object') return
    const page = item as Record<string, unknown>
    const pageId = typeof page.pageId === 'string' && page.pageId.trim()
      ? page.pageId.trim()
      : `page-${index + 1}`
    const status = typeof page._change_status === 'string' ? page._change_status : ''
    if (status === 'added' || status === 'modified' || status === 'unchanged' || status === 'deleted') {
      map.set(pageId, status)
    }
  })
  return map
}

/**
 * 解析某个页面的变更徽标状态：
 * - 有 specDiff 且该页有标记 → 用标记；
 * - specDiff 完全没有值（首轮创建）→ 全部按"新增"；
 * - specDiff 有值但该页缺标记 → 兜底按"未变更"。
 */
function resolvePageStatus(
  status: ChangeStatus | undefined,
  hasDiff: boolean
): ChangeStatus {
  return status ?? (hasDiff ? 'unchanged' : 'added')
}

type PageDesignArea = 'interaction' | 'layout'

/** 读取后端按 ProductPlan 子树计算的交互/布局状态；没有该区域数据时由页面级状态兜底。 */
function pageAreaChangeStatus(
  specDiff: JsonRecord | undefined,
  pageId: string,
  area: PageDesignArea
): ChangeStatus | undefined {
  const overview = specDiff?._page_design_overview
  if (!overview || typeof overview !== 'object' || Array.isArray(overview)) return undefined
  const page = (overview as Record<string, unknown>)[pageId]
  if (!page || typeof page !== 'object' || Array.isArray(page)) return undefined
  const status = (page as Record<string, unknown>)[area]
  return status === 'added' || status === 'modified' || status === 'unchanged' || status === 'deleted'
    ? status
    : undefined
}

/**
 * 渲染「页面概览」版块：聚合页面交互 / 页面布局 / 页面风格三块，紧凑行展示。
 * 交互/布局来自 ProductPlan，风格来自 RequirementSpec 自由文本。每块用彩色标题条
 * + 行内标签，减少嵌套卡片层数。
 *
 * 2026-10-21 从技术规划面板迁移到设计阶段需求文档面板（RequirementDocPanel），
 * 数据全部来自上游 ProductPlan/RequirementSpec，与技术规划 TechnicalPlan 无关。
 */
export default function PageDesignOverviewSection({
  productPlan,
  spec,
  specBaseline,
  specDiff,
  sectionKey
}: Props): ReactElement {
  // 三块拍平：交互/布局来自 productPlan，风格来自 spec 自由文本。
  const diffPages = recordItems(specDiff?.pages)
  const interactions = requirementInteractionRows(productPlan, diffPages)
  const layouts = requirementLayoutRows(productPlan, diffPages)
  const styleHints = requirementStyleHints(spec)
  const baselineStyleHints = specBaseline ? requirementStyleHints(specBaseline) : undefined
  // 页面身份与需求文档"页面"页签一致：都按 pageId 对齐 ProductPlan 与 specDiff。
  const statusByPageId = pageChangeStatusMap(specDiff)
  const hasDiff = Boolean(specDiff)
  const pageStatus = (pageId: string, area: PageDesignArea): ChangeStatus =>
    resolvePageStatus(
      pageAreaChangeStatus(specDiff, pageId, area) ?? statusByPageId.get(pageId),
      hasDiff
    )
  // 应用级变更标记（后端 `_app_change_status`）：风格维度取自需求全文，
  // 没有页面归属，只能按应用级结论打标。
  const appStatus = resolveAppStatus(specDiff)
  return (
    <section
      aria-label="页面概览"
      className={cx('technical-plan-section', 'page-design-overview')}
      id={`page-design-overview-panel-${sectionKey}`}
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
                  <ChangeBadge changeStatus={pageStatus(interaction.pageId, 'interaction')} />
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
                  <ChangeBadge changeStatus={pageStatus(layout.pageId, 'layout')} />
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
                  {/* 风格维度来自需求全文（应用级事实），用后端的应用级变更标记，
                      而不是某个页面的变更状态。 */}
                  <ChangeBadge changeStatus={resolveStyleStatus(hint, baselineStyleHints, appStatus)} />
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
