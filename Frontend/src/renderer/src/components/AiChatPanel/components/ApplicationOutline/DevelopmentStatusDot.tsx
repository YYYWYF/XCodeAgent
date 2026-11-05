import type { DevelopmentArtifactProgress } from '../../../../typings'
import { developmentDisplayStatus, developmentStatusLabel } from '../../../../developmentArtifacts'
import { cx } from '../../../../utils'
import './DevelopmentStatusDot.less'

/** 接口按正式设计确认状态绘制圆点，其他产物保留开发状态，并提供悬浮及无障碍说明。 */
export default function DevelopmentStatusDot({
  progress,
  designConfirmed,
  statusLabel
}: {
  progress?: DevelopmentArtifactProgress
  designConfirmed?: boolean
  statusLabel?: string
}): JSX.Element {
  const label = statusLabel || (designConfirmed === undefined ? developmentStatusLabel(progress)
    : designConfirmed ? '当前接口映射或接入设计已确认' : '当前接口映射或接入设计待确认')
  const status = designConfirmed === undefined ? developmentDisplayStatus(progress)
    : designConfirmed ? 'completed' : 'pending'
  return (
    <span
      aria-label={label}
      className={cx('development-status-dot')}
      data-status={status}
      role="img"
      title={label}
    />
  )
}
