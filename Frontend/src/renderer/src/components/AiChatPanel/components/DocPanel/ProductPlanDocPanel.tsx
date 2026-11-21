import { CheckCircleOutlined, FileTextOutlined } from '@ant-design/icons'
import { Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import { RequirementPagesSection } from './RequirementDocSections'
import {
  asRecord,
  recordItems,
  stringItems,
  textValue,
  type JsonRecord
} from './RequirementDocPanelData'
import './RequirementDocPanel.less'

/** 产品规划复用设计阶段页面卡片，并保留应用摘要、确认状态与产品级验收标准。
 *
 *  `productPlan.pages[]._change_status` 由后端从 RequirementSpec 同步而来，
 *  与需求文档（草稿）共用同一套"新增/修改/删除/继承自基线"徽标与分区逻辑。
 */
export default function ProductPlanDocPanel({
  productPlan
}: {
  productPlan: JsonRecord
}): ReactElement {
  const app = asRecord(productPlan.app)
  const criteria = stringItems(productPlan.product_acceptance_criteria)
  const status = productPlan.confirmation_status
  const statusLabel =
    status === 'confirmed' ? '已确认' : status === 'pending_user_confirmation' ? '待确认' : '草稿'
  // 页面变更标记直接来自产品规划自身（后端已同步需求侧结论）；
  // 有任一标记即视为跨迭代场景，页面 tab 据此分区展示。
  const changeStatusMarks = recordItems(productPlan.pages).some(
    (page) => typeof page._change_status === 'string' && page._change_status
  )
  const specDiff = changeStatusMarks ? { pages: recordItems(productPlan.pages) } : undefined
  return (
    <div className={cx('requirement-doc-panel')}>
      <div className={cx('requirement-doc-section-title')}>
        <FileTextOutlined /> <span>产品概览</span>
      </div>
      <article className={cx('requirement-doc-app-card')}>
        <strong>{textValue(app.name, '未命名应用')}</strong>
        {textValue(app.summary) ? (
          <Typography.Text type="secondary">{textValue(app.summary)}</Typography.Text>
        ) : null}
        <div className={cx('requirement-doc-app-meta')}>
          <Tag>{statusLabel}</Tag>
          {textValue(productPlan.version) ? <Tag>{textValue(productPlan.version)}</Tag> : null}
        </div>
      </article>
      <RequirementPagesSection
        productPlan={productPlan}
        sectionKey="pages"
        spec={{}}
        specDiff={specDiff}
      />
      <div className={cx('requirement-doc-section-title')}>
        <CheckCircleOutlined /> <span>产品级验收标准</span>
      </div>
      {criteria.length ? (
        <ul className={cx('requirement-doc-page-rules', 'requirement-doc-acceptance')}>
          {criteria.map((criterion, index) => (
            <li key={`${index}-${criterion}`}>{criterion}</li>
          ))}
        </ul>
      ) : (
        <Typography.Text type="secondary">暂无产品级验收标准</Typography.Text>
      )}
    </div>
  )
}
