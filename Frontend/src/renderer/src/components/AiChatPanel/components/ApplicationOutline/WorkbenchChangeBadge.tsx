import type { ReactElement } from 'react'
import { cx } from '../../../../utils'

export type WorkbenchChangeStatus = 'added' | 'modified' | 'unchanged' | 'deleted'

/** 徽标文案：与需求文档（草稿）保持同一套说法。 */
const LABELS: Record<WorkbenchChangeStatus, string> = {
  added: '新增',
  modified: '有变更',
  unchanged: '未变更',
  // 开发阶段它还没有真正清理掉，只是本轮产物清单里的待处理状态，所以叫"待删除"。
  deleted: '待删除'
}

/** 悬停说明，让用户知道这个标记的含义。 */
const TITLES: Record<WorkbenchChangeStatus, string> = {
  added: '本轮迭代新增',
  modified: '本轮迭代有变更（与基线版本不同）',
  unchanged: '本轮迭代未变更，继承自基线',
  deleted: '本轮迭代删除，开发阶段可点击清理它的代码与设计稿'
}

/** 工作台产物（页面 / 接口 / 实体）的变更类型徽标。 */
export function WorkbenchChangeBadge({
  status
}: {
  status?: WorkbenchChangeStatus
}): ReactElement | null {
  if (!status) return null
  return (
    <span
      className={cx('workbench-change-badge', `is-${status}`)}
      title={TITLES[status]}
    >
      {LABELS[status]}
    </span>
  )
}

/** 已删除的产物不允许进入开发流程。 */
export function isDeletedStatus(status?: WorkbenchChangeStatus): boolean {
  return status === 'deleted'
}
