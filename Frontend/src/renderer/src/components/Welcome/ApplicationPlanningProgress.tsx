import { CheckOutlined } from '@ant-design/icons'
import { Typography } from 'antd'
import type { PagePlanningProgress } from '../../typings'
import { cx } from '../../utils'

const { Text } = Typography

const STAGE_LABELS: Record<string, string> = {
  analyzing_context: '理解应用场景',
  generating_questions: '整理关键问题',
  validating_questions: '校验问题质量',
  analyzing_requirements: '分析业务需求',
  designing_pages: '规划页面目录',
  designing_interactions_and_apis: '设计交互与 API',
  validating_plan: '校验方案关系',
  validating_confirmation: '校验确认内容',
  persisting_application: '写入应用配置'
}

type Props = {
  events: PagePlanningProgress[]
  fallbackMessage: string
  streamingContent?: string
  title: string
}

// 追加或更新同一阶段的 AG-UI 进度，保留完整阶段历史且避免重复事件。
export function appendPagePlanningProgress(
  history: PagePlanningProgress[],
  nextProgress: PagePlanningProgress
): PagePlanningProgress[] {
  const existingIndex = history.findIndex((item) => item.stage === nextProgress.stage)
  if (existingIndex < 0) return [...history, nextProgress]
  return history.map((item, index) => index === existingIndex ? nextProgress : item)
}

// 将 AG-UI 进度事件渲染成紫色动态进度轨、阶段时间线和实时消息。
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
              <p key={`${index}-${line}`}><span>›</span>{line}</p>
            ))}
          </div>
        </div>
      ) : null}
    </section>
  )
}
