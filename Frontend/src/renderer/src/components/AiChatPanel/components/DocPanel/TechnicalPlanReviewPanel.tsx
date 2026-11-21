import { useMemo } from 'react'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import TechnicalPlanDocPanel from './TechnicalPlanDocPanel'
import { technicalPlanDiff } from './technicalPlanDiff'
import type { JsonRecord } from './TechnicalPlanDocPanelData'
import './TechnicalPlanReviewPanel.less'

type Props = {
  plan: JsonRecord
  productPlan: JsonRecord
  baseline?: JsonRecord
  updating?: boolean
  onReviseAuthorization?: () => void
}

/** 首轮创建用的空基线：所有列表为空、架构为空对象，使每一项都被判为"新增"。 */
const EMPTY_TECHNICAL_PLAN: JsonRecord = {
  artifact_type: 'technical-plan',
  architecture: {},
  entities: [],
  api_contracts: [],
  pages: [],
  authorization_manifest: {}
}

/** 渲染技术规划文档：单文档视图，变更内容在上、继承自基线的未变更内容在下。
 *
 *  与需求文档（草稿）保持同一套展示口径：不提供"本次变更 / 完整规划"阅读模式切换，
 *  各 section 内部自己分区，并给每个对象打"新增 / 修改 / 继承自基线 / 删除"徽标。
 */
export default function TechnicalPlanReviewPanel({
  plan,
  productPlan,
  baseline,
  updating,
  onReviseAuthorization
}: Props): ReactElement {
  // 首轮创建没有基线：按"全部新增"展示，与需求文档侧 `_tag_all_items(..., 'added')` 一致。
  // 用空对象当对比基线即可 —— `changedObjects` 会把基线为空壳的对象判为"新增"。
  const hasBaseline = Boolean(baseline && Object.keys(baseline).length)
  const comparisonBase = hasBaseline ? baseline : EMPTY_TECHNICAL_PLAN
  const groups = useMemo(
    () => technicalPlanDiff(comparisonBase!, plan, productPlan),
    [comparisonBase, plan, productPlan]
  )
  return (
    <div className={cx('technical-plan-review')}>
      <TechnicalPlanDocPanel
        plan={plan}
        productPlan={productPlan}
        changes={groups}
        hasBaseline={hasBaseline}
        updating={updating}
        onReviseAuthorization={onReviseAuthorization}
      />
    </div>
  )
}
