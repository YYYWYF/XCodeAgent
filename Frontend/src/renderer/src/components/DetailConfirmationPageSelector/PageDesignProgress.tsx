import { CheckOutlined, LoadingOutlined } from '@ant-design/icons'
import { Typography } from 'antd'
import { useMemo } from 'react'
import { useProgressivePercent } from '../../hooks/useProgressivePercent'
import type { WorkflowEvent } from '../../typings'
import { cx } from '../../utils'
import './PageDesignProgress.less'

const { Text } = Typography

const DESIGN_STAGES = [
  { label: '准备设计上下文', detail: '读取页面目标、路由与已有项目规划', target: 12 },
  { label: '分析页面结构', detail: '梳理内容层级、关键区域与用户路径', target: 34 },
  { label: '设计布局与交互', detail: '生成布局、操作反馈与页面跳转方案', target: 58 },
  { label: '补全状态与数据', detail: '完善加载、空态、异常态及数据依赖', target: 78 },
  { label: '整理验收方案', detail: '汇总设计结果与可确认的验收标准', target: 92 }
] as const

const ENDPOINT_DESIGN_STAGES = [
  { label: '定位接口契约', detail: '读取 API Contract、Method、Path 与 Schema 引用', target: 18 },
  { label: '分析请求参数', detail: '梳理路径参数、查询参数、请求头和请求体', target: 38 },
  { label: '确认数据来源', detail: '设计第三方接口、MySQL 表或新增表字段来源', target: 58 },
  { label: '设计返回格式', detail: '整理响应结构、状态码、错误响应与字段映射', target: 78 },
  { label: '整理接口验收', detail: '汇总接口详细设计与后续任务拆分依据', target: 92 }
] as const

const ENTITY_DESIGN_STAGES = [
  { label: '读取实体定义', detail: '读取已确认实体的字段、类型与数据源绑定', target: 22 },
  { label: '设计字段与类型', detail: '整理字段语义类型、必填约束与枚举取值', target: 45 },
  { label: '设计表结构与规则', detail: '生成目标表结构、唯一编码约束与业务规则', target: 72 },
  { label: '整理实体验收', detail: '汇总实体详细设计与待确认事项', target: 92 }
] as const

type Props = {
  events?: WorkflowEvent[]
  pageLabel: string
  targetType?: 'page' | 'endpoint' | 'entity'
}

/** 判断页面细节节点是否已经由后端报告完成。 */
function detailDesignCompleted(events: WorkflowEvent[]): boolean {
  return events.some(
    (event) =>
      event.type === 'workflow.node.completed' && event.nodeName === 'development_readiness_gate'
  )
}

/** 根据唯一的百分比状态定位当前步骤，保证步骤条不会与进度条错位。 */
function stageIndexForPercent<T extends readonly { target: number }[]>(percent: number, stages: T): number {
  const nextStageIndex = stages.findIndex((stage) => percent <= stage.target)
  return nextStageIndex < 0 ? stages.length - 1 : nextStageIndex
}

/** 读取后端细节设计进度事件，用真实阶段消息覆盖本地模拟文案。 */
function latestDetailProgressEvent(events: WorkflowEvent[]): WorkflowEvent | undefined {
  return [...events].reverse().find(
    (event) =>
      event.type === 'workflow.node.progress' &&
      event.nodeName === 'development_readiness_gate' &&
      event.message
  )
}

/** 判断当前进度是否来自 endpoint 详细设计，避免接口生成时复用页面文案。 */
function progressTargetType(
  events: WorkflowEvent[],
  fallback?: 'page' | 'endpoint' | 'entity'
): 'page' | 'endpoint' | 'entity' {
  const progress = latestDetailProgressEvent(events)
  const detail = progress?.data?.detail
  const targetType =
    detail && typeof detail === 'object'
      ? (detail as Record<string, unknown>).target_type
      : undefined
  return targetType === 'endpoint'
    ? 'endpoint'
    : targetType === 'entity'
      ? 'entity'
      : fallback || 'page'
}

/** 在后端细粒度阶段不可见时，让百分比与步骤条使用同一个推进状态。 */
export default function PageDesignProgress({
  events = [],
  pageLabel,
  targetType
}: Props): JSX.Element {
  const completed = detailDesignCompleted(events)
  const resolvedTargetType = progressTargetType(events, targetType)
  const stages =
    resolvedTargetType === 'endpoint'
      ? ENDPOINT_DESIGN_STAGES
      : resolvedTargetType === 'entity'
        ? ENTITY_DESIGN_STAGES
        : DESIGN_STAGES
  const latestProgress = latestDetailProgressEvent(events)
  const activityKey = useMemo(
    () => events.reduce((total, event) => total + (event.message?.length || 1), 0),
    [events]
  )
  const percent = useProgressivePercent(completed ? 100 : 6, completed ? 100 : 98, activityKey)
  const stageIndex = stageIndexForPercent(percent, stages)
  const stage = stages[stageIndex]
  const currentLabel = latestProgress?.message || stage.label

  return (
    <section aria-live="polite" className={cx('detail-page-progress')}>
      <div className={cx('detail-page-progress-head')}>
        <Text className={cx('detail-page-progress-title')} strong>
          正在设计「{pageLabel}」
        </Text>
        <Text
          aria-label={`${
            resolvedTargetType === 'endpoint'
              ? '接口'
              : resolvedTargetType === 'entity'
                ? '实体'
                : '页面'
          }设计进度 ${percent}%`}
          aria-valuemax={100}
          aria-valuemin={0}
          aria-valuenow={percent}
          className={cx('detail-page-progress-percent')}
          role="progressbar"
        >
          {percent}%
        </Text>
      </div>

      <div className={cx('detail-page-progress-status')}>
        <span className={cx('detail-page-progress-status-icon')} aria-hidden="true">
          {completed ? <CheckOutlined /> : <LoadingOutlined spin />}
        </span>
        <div className={cx('detail-page-progress-status-text')}>
          <Text className={cx('detail-page-progress-current')}>{currentLabel}</Text>
          <Text className={cx('detail-page-progress-detail')} type="secondary">
            {stage.detail}
          </Text>
        </div>
      </div>

      <div
        className={cx('detail-page-progress-stepper')}
        style={{ gridTemplateColumns: `repeat(${stages.length}, minmax(0, 1fr))` }}
      >
        {stages.map((item, index) => {
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
