import type { DevelopmentArtifactProgress } from '../../../../typings'
import { developmentStatusLabel } from '../../../../developmentArtifacts'
import { cx } from '../../../../utils'
import './DevelopmentStatusDot.less'

/** 使用同一初次开发状态绘制圆点，并提供文字说明供辅助技术与悬浮提示读取。 */
export default function DevelopmentStatusDot({
  progress
}: {
  progress?: DevelopmentArtifactProgress
}): JSX.Element {
  const label = developmentStatusLabel(progress)
  return (
    <span
      aria-label={label}
      className={cx('development-status-dot')}
      data-status={progress?.initialDevelopmentStatus || 'pending'}
      role="img"
      title={label}
    />
  )
}
