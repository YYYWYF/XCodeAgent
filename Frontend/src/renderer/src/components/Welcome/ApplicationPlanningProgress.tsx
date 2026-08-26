import { CheckOutlined } from '@ant-design/icons'
import { Typography } from 'antd'
import { cx } from '../../utils'

const { Text } = Typography

const STAGE_LABELS: Record<string, string> = {
  requirements: '需求确认',
  product_planning: '产品规划',
  ui_confirmation: 'UI确认',
  technical_planning: '技术规划',
  application_template: '应用模板'
}

export type ApplicationPlanningProgressEvent = {
  stage: string
  percent: number
  message: string
  detail?: string
}

type Props = {
  events: ApplicationPlanningProgressEvent[]
  fallbackMessage: string
  streamingContent?: string
  title: string
}

// 使用原创建规划页面的动态视觉展示各节点进度、时间线与 AG-UI 实时消息。
export default function ApplicationPlanningProgress({
  events,
  fallbackMessage,
  streamingContent,
  title
}: Props): JSX.Element {
  const current = events[events.length - 1]
  const percent = current?.percent ?? 6
  const streamLines = (streamingContent || '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .filter((line, index, lines) => lines.indexOf(line) === index)

  return (
    <section aria-live="polite" className={cx('planning-progress-card')}>
      <div className={cx('planning-progress-hero')}>
        <span aria-hidden className={cx('planning-progress-orbit')}>
          <span />
        </span>
        <div className={cx('planning-progress-copy')}>
          <Text className={cx('planning-progress-eyebrow')}>{title}</Text>
          <Text className={cx('planning-progress-current')}>
            {current?.message || fallbackMessage}
          </Text>
          {current?.detail ? (
            <Text className={cx('planning-progress-detail')}>{current.detail}</Text>
          ) : null}
        </div>
        <span className={cx('planning-progress-percent')}>{percent}%</span>
      </div>

      <div
        aria-label={`当前进度 ${percent}%`}
        aria-valuemax={100}
        aria-valuemin={0}
        aria-valuenow={percent}
        className={cx('planning-progress-track')}
        role="progressbar"
      >
        <span className={cx('planning-progress-bar')} style={{ width: `${percent}%` }}>
          <span className={cx('planning-progress-glow')} />
        </span>
      </div>

      {events.length ? (
        <div className={cx('planning-progress-timeline')}>
          {events.map((event, index) => {
            const active = index === events.length - 1
            return (
              <div
                className={cx('planning-progress-stage', active && 'is-active')}
                key={event.stage}
              >
                <span className={cx('planning-progress-stage-icon')}>
                  {active ? <span className={cx('planning-progress-pulse')} /> : <CheckOutlined />}
                </span>
                <div>
                  <div className={cx('planning-progress-stage-heading')}>
                    <Text strong>{STAGE_LABELS[event.stage] || event.stage}</Text>
                  </div>
                  <Text className={cx('planning-progress-stage-message')}>{event.message}</Text>
                  {event.detail ? <Text type="secondary">{event.detail}</Text> : null}
                </div>
              </div>
            )
          })}
        </div>
      ) : null}

      {streamLines.length ? (
        <div className={cx('planning-progress-messages')}>
          <Text className={cx('planning-progress-messages-title')}>AG-UI 实时消息</Text>
          <div>
            {streamLines.map((line, index) => (
              <p key={`${index}-${line}`}>
                <span>›</span>
                {line}
              </p>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  )
}
