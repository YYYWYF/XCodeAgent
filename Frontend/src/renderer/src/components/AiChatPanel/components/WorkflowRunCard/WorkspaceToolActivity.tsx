import { CheckCircleOutlined, CloseCircleOutlined, LoadingOutlined } from '@ant-design/icons'
import { Typography } from 'antd'
import type { ReactElement } from 'react'
import type { WorkflowBuildToolActivity } from '../../../../typings'
import { cx } from '../../../../utils'
import './WorkspaceToolActivity.less'

const { Text } = Typography

export default function WorkspaceToolActivity({
  activity
}: {
  activity: WorkflowBuildToolActivity
}): ReactElement {
  /** 以单行高亮样式展示当前任务最新工具操作，不展开原始工具参数。 */

  return (
    <div
      aria-label={activity.message}
      aria-live="polite"
      className={cx('workflow-build-tool-activity', activity.status)}
      title={activity.message}
    >
      <span className={cx('workflow-build-tool-activity-icon')} aria-hidden="true">
        {activity.status === 'running' ? (
          <LoadingOutlined spin />
        ) : activity.status === 'completed' ? (
          <CheckCircleOutlined />
        ) : (
          <CloseCircleOutlined />
        )}
      </span>
      <Text>{activity.message}</Text>
    </div>
  )
}
