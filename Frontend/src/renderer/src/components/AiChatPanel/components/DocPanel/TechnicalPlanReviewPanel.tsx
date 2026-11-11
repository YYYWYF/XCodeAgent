import { useMemo, useState } from 'react'
import type { ReactElement } from 'react'
import { Radio } from 'antd'
import { cx } from '../../../../utils'
import TechnicalPlanDocPanel from './TechnicalPlanDocPanel'
import { technicalPlanDiff, differenceCode, type ChangeGroup } from './technicalPlanDiff'
import type { JsonRecord } from './TechnicalPlanDocPanelData'
import './TechnicalPlanReviewPanel.less'

type Props = {
  plan: JsonRecord
  productPlan: JsonRecord
  baseline?: JsonRecord
  updating?: boolean
  onReviseAuthorization?: () => void
}

/** 分组展示变化，上方解释字段位置，下方用原始属性和值核对增删内容。 */
function ChangeList({ groups }: { groups: ChangeGroup[] }): ReactElement {
  const count = groups.reduce((total, group) => total + group.objects.length, 0)
  return (
    <div className={cx('technical-plan-changes')} aria-label="技术规划本次变更">
      <div className={cx('technical-plan-change-summary')}>
        <strong>{count ? `${count} 个对象有变化` : '本次未产生内容变化'}</strong>
        <span>对比：本次修改前 → 本次生成</span>
      </div>
      {groups
        .filter((group) => group.objects.length)
        .map((group) => (
          <section key={group.name}>
            <h3>
              {group.name} · {group.objects.length} 个对象
            </h3>
            {group.objects.map((object, index) => (
              <details key={object.id} open={index === 0}>
                <summary>
                  <strong>{object.name}</strong>
                  <span className={cx('technical-plan-change-kind')}>{object.kind}</span>
                  <span>{object.differences.length} 处</span>
                </summary>
                <div className={cx('technical-plan-change-details')}>
                  {object.differences.map((difference, changeIndex) => (
                    <div
                      className={cx('technical-plan-change-field')}
                      key={`${difference.path}:${changeIndex}`}
                    >
                      <div className={cx('technical-plan-change-path')}>{difference.path}</div>
                      {difference.kind === 'rename' ? (
                        <div className={cx('technical-plan-name-change')}>
                          <span>名称变更</span>
                          <code>{String(difference.before)}</code>
                          <span aria-label="改为">→</span>
                          <code>{String(difference.after)}</code>
                          {difference.unchanged ? <span>其余属性未变</span> : null}
                        </div>
                      ) : (
                        <>
                          {difference.before !== undefined ? (
                            <div className={cx('technical-plan-change-value', 'is-before')}>
                              <span>− {difference.after === undefined ? '删除' : '修改前'}</span>
                              <code>{differenceCode(difference.field, difference.before)}</code>
                            </div>
                          ) : null}
                          {difference.after !== undefined ? (
                            <div className={cx('technical-plan-change-value', 'is-after')}>
                              <span>+ {difference.before === undefined ? '新增' : '修改后'}</span>
                              <code>{differenceCode(difference.field, difference.after)}</code>
                            </div>
                          ) : null}
                        </>
                      )}
                    </div>
                  ))}
                </div>
              </details>
            ))}
          </section>
        ))}
      <div className={cx('technical-plan-change-unchanged')}>
        {groups
          .filter((group) => !group.objects.length)
          .map((group) => group.name)
          .join('、')}
        {groups.some((group) => !group.objects.length) ? '无变化' : ''}
      </div>
    </div>
  )
}

/** 在同一文档区切换变更审阅与完整规划，所有操作仅改变本地阅读状态。 */
export default function TechnicalPlanReviewPanel({
  plan,
  productPlan,
  baseline,
  updating,
  onReviseAuthorization
}: Props): ReactElement {
  const canCompare = Boolean(baseline && Object.keys(baseline).length) && !updating
  const groups = useMemo(
    () => (canCompare ? technicalPlanDiff(baseline!, plan, productPlan) : []),
    [canCompare, baseline, plan, productPlan]
  )
  const count = groups.reduce((total, group) => total + group.objects.length, 0)
  const [mode, setMode] = useState<'changes' | 'full'>('changes')
  return (
    <div className={cx('technical-plan-review')}>
      {canCompare ? (
        <Radio.Group
          aria-label="技术规划阅读模式"
          value={mode}
          onChange={(event) => setMode(event.target.value)}
        >
          <Radio.Button value="changes">本次变更 · {count}</Radio.Button>
          <Radio.Button value="full">完整规划</Radio.Button>
        </Radio.Group>
      ) : null}
      {canCompare && mode === 'changes' ? (
        <ChangeList groups={groups} />
      ) : (
        <div className={cx('technical-plan-full-view')}>
          {canCompare && count ? (
            <div className={cx('technical-plan-change-context')}>
              本次修改涉及{' '}
              {groups
                .filter((group) => group.objects.length)
                .map((group) => `${group.name} ${group.objects.length} 项`)
                .join('、')}
            </div>
          ) : null}
          <TechnicalPlanDocPanel
            plan={plan}
            productPlan={productPlan}
            changes={canCompare ? groups : undefined}
            onReviseAuthorization={onReviseAuthorization}
          />
        </div>
      )}
    </div>
  )
}
