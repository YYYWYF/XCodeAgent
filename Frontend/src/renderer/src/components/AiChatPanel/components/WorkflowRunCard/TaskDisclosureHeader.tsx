import {
  ApiOutlined,
  BranchesOutlined,
  DownOutlined,
  FileTextOutlined,
  FolderOutlined,
  InfoCircleOutlined,
  UnorderedListOutlined
} from '@ant-design/icons'
import { Typography } from 'antd'
import type { ReactNode } from 'react'
import { cx } from '../../../../utils'
import type { TaskFileChange } from './buildTaskPresentation'
import { TaskFileOperationBadge } from './TaskFileOperationBadge'

/** 展示二级折叠项名称与内容数量，帮助用户判断是否需要展开。 */
export function TaskDisclosureHeader({
  count,
  label,
  unit = '项',
  files,
  summary
}: {
  count: number
  label: string
  unit?: string
  files?: TaskFileChange[]
  summary?: ReactNode
}): JSX.Element {
  const icons = {
    涉及接口: <ApiOutlined />,
    依赖任务: <BranchesOutlined />,
    文件改动: <FileTextOutlined />,
    允许修改范围: <FolderOutlined />,
    待验收: <UnorderedListOutlined />,
    业务检查: <UnorderedListOutlined />,
    工程检查: <UnorderedListOutlined />,
    执行信息: <InfoCircleOutlined />
  }
  // 按去重后的明确文件统计操作；目录、通配范围和缺失操作不推测为新增。
  const operationCounts = ['add', 'modify', 'delete', 'rename']
    .map((operation) => ({
      operation,
      count: files?.filter((file) => file.operations.includes(operation)).length || 0
    }))
    .filter((item) => item.count > 0)
  return (
    <span className={cx('workflow-dag-confirmation-disclosure-header')}>
      <span className={cx('workflow-dag-confirmation-disclosure-label')}>
        <span className={cx('workflow-dag-confirmation-disclosure-icon')} aria-hidden="true">
          {icons[label as keyof typeof icons]}
        </span>
        <Typography.Text>{label}</Typography.Text>
        <small>
          {count} {unit}
        </small>
      </span>
      {summary && (
        <span className={cx('workflow-dag-confirmation-disclosure-summary')}>{summary}</span>
      )}
      {operationCounts.length > 0 && (
        <span className={cx('workflow-dag-confirmation-disclosure-operations')}>
          {operationCounts.map((item) => (
            <TaskFileOperationBadge key={item.operation} {...item} />
          ))}
        </span>
      )}
    </span>
  )
}

/** 统一二级折叠区与文件的向下箭头，展开后朝上，沿用 Ant Design 的键盘交互。 */
export function taskDisclosureExpandIcon({ isActive }: { isActive?: boolean }): JSX.Element {
  return <DownOutlined rotate={isActive ? 180 : 0} aria-hidden="true" />
}
