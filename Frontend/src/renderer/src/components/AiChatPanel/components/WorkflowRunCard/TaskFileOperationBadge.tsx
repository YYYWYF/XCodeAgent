import { cx } from '../../../../utils'
import './TaskFileOperationBadge.less'

const operationLabels: Record<string, string> = {
  add: '新增',
  modify: '修改',
  delete: '删除',
  rename: '重命名'
}

/** 统一文件行及数量摘要的操作配色，保留文字以避免仅依赖颜色辨识。 */
export function TaskFileOperationBadge({
  operation,
  count
}: {
  operation: string
  count?: number
}): JSX.Element {
  return (
    <span className={cx('workflow-task-file-operation')} data-operation={operation}>
      {operationLabels[operation] || operation}
      {count !== undefined ? ` ${count}` : ''}
    </span>
  )
}
