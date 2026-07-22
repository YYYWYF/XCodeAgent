import { CheckOutlined, LoadingOutlined } from '@ant-design/icons'
import { Typography } from 'antd'
import { useMemo } from 'react'
import { useProgressivePercent } from '../../hooks/useProgressivePercent'
import type { WorkflowEvent } from '../../typings'
import { cx } from '../../utils'
import './PageDesignProgress.less'

const { Text, Title } = Typography

const DESIGN_STAGES = [
  { label: '准备设计上下文', detail: '读取页面目标、路由与已有项目规划', target: 12 },
  { label: '分析页面结构', detail: '梳理内容层级、关键区域与用户路径', target: 34 },
  { label: '设计布局与交互', detail: '生成布局、操作反馈与页面跳转方案', target: 58 },
  { label: '补全状态与数据', detail: '完善加载、空态、异常态及数据依赖', target: 78 },
  { label: '整理验收方案', detail: '汇总设计结果与可确认的验收标准', target: 92 }
] as const

type Props = {
  events?: WorkflowEvent[]
  pageLabel: string
}

/** 判断页面细节节点是否已经由后端报告完成。 */
function detailDesignCompleted(events: WorkflowEvent[]): boolean {
  return events.some(
    (event) => event.type === 'workflow.node.completed' && event.nodeName === 'detail_confirmation'
  )
}

/** 根据唯一的百分比状态定位当前步骤，保证步骤条不会与进度条错位。 */
function stageIndexForPercent(percent: number): number {
  const nextStageIndex = DESIGN_STAGES.findIndex((stage) => percent <= stage.target)
  return nextStageIndex < 0 ? DESIGN_STAGES.length - 1 : nextStageIndex
}

/** 在后端细粒度阶段不可见时，让百分比与步骤条使用同一个推进状态。 */
export default function PageDesignProgress({ events = [], pageLabel }: Props): JSX.Element {
  const completed = detailDesignCompleted(events)
  const activityKey = useMemo(
    () => events.reduce((total, event) => total + (event.message?.length || 1), 0),
    [events]
  )
  const percent = useProgressivePercent(completed ? 100 : 6, completed ? 100 : 98, activityKey)
  const stageIndex = stageIndexForPercent(percent)
  const stage = DESIGN_STAGES[stageIndex]

  return (
    <section aria-live="polite" className={cx('detail-page-progress')}>
      <div className={cx('detail-page-progress-visual')} aria-hidden>
        <span className={cx('detail-page-progress-orbit')}>
          <span />
        </span>
        <LoadingOutlined className={cx('detail-page-progress-loading')} />
      </div>

      <Text className={cx('detail-page-selector-eyebrow')}>GENERATING PAGE DESIGN</Text>
      <Title level={3}>正在设计「{pageLabel}」</Title>
      <Text className={cx('detail-page-progress-current')}>{stage.label}</Text>
      <Text className={cx('detail-page-progress-detail')} type="secondary">
        {stage.detail}
      </Text>

      <div className={cx('detail-page-progress-summary')}>
        <Text>设计进度</Text>
        <Text strong>{percent}%</Text>
      </div>
      <div
        aria-label={`页面设计进度 ${percent}%`}
        aria-valuemax={100}
        aria-valuemin={0}
        aria-valuenow={percent}
        className={cx('detail-page-progress-track')}
        role="progressbar"
      >
        <span className={cx('detail-page-progress-bar')} style={{ width: `${percent}%` }}>
          <span />
        </span>
      </div>

      <div className={cx('detail-page-progress-stages')}>
        {DESIGN_STAGES.map((item, index) => {
          const isDone = completed || index < stageIndex
          const isActive = !completed && index === stageIndex
          return (
            <div
              className={cx(
                'detail-page-progress-stage',
                isDone && 'is-done',
                isActive && 'is-active'
              )}
              key={item.label}
            >
              <span className={cx('detail-page-progress-stage-icon')}>
                {isDone ? <CheckOutlined /> : index + 1}
              </span>
              <Text>{item.label}</Text>
            </div>
          )
        })}
      </div>

      <Text className={cx('detail-page-progress-hint')} type="secondary">
        最终完成状态以工作流返回结果为准
      </Text>
    </section>
  )
}
