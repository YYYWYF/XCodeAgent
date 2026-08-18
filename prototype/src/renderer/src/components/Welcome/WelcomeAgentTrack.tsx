import { cx } from '../../utils'
import './WelcomeAgentTrack.less'

const stages = [{ label: '设计阶段' }, { label: '开发阶段' }, { label: '审查阶段' }]

/** 展示与工作台一致的应用三阶段，仅承担欢迎页的轻量说明作用。 */
export default function WelcomeAgentTrack(): JSX.Element {
  return (
    <section className={cx('agent-preview')} aria-label="应用制作阶段">
      <div className={cx('agent-stages')}>
        {stages.map((stage, index) => (
          <div className={cx('agent-stage')} key={stage.label}>
            <span>0{index + 1}</span>
            <strong>{stage.label}</strong>
            {index < stages.length - 1 ? <i aria-hidden="true" /> : null}
          </div>
        ))}
      </div>
    </section>
  )
}
