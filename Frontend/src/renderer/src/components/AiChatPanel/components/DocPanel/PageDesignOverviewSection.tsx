import { BgColorsOutlined, InteractionOutlined, LayoutOutlined } from '@ant-design/icons'
import { Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import {
  requirementInteractionRows,
  requirementLayoutRows,
  requirementStyleHints
} from './RequirementDocPanelData'
import type { JsonRecord } from './TechnicalPlanDocPanelData'
import './TechnicalPlanDocPanel.less'

const { Text } = Typography

type Props = {
  productPlan: JsonRecord
  spec: JsonRecord
  /** 触发 aria id 与 tab 对齐的 section key（如 'page-design-overview'）。 */
  sectionKey: string
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
  sectionKey
}: Props): ReactElement {
  // 三块拍平：交互/布局来自 productPlan，风格来自 spec 自由文本。
  const interactions = requirementInteractionRows(productPlan)
  const layouts = requirementLayoutRows(productPlan)
  const styleHints = requirementStyleHints(spec)
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
