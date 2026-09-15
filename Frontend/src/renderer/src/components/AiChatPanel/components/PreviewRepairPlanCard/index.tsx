import {
  DownOutlined,
  FileTextOutlined,
  SafetyCertificateOutlined,
  ToolOutlined
} from '@ant-design/icons'
import type { ReactElement } from 'react'
import { useId, useState } from 'react'
import MarkdownContent from '../../../MarkdownContent/MarkdownContent'
import './PreviewRepairPlanCard.less'

type PreviewRepairPlanCardProps = {
  content: string
}

type RepairPlanSections = {
  activity: string[]
  body: string
}

/** 合并连续重复的进度，避免同一阶段重复上报时撑高对话。 */
function uniqueActivity(lines: string[]): string[] {
  return lines.reduce<string[]>((result, line) => {
    const normalized = line.replace(/\s+/g, ' ').trim()
    if (!normalized || result.at(-1) === normalized) return result
    result.push(normalized)
    return result
  }, [])
}

/** 把修复计划标题前的 AG-UI 进度与正式计划正文分开。 */
function planSections(content: string): RepairPlanSections {
  const normalized = content.replace(/\r\n?/g, '\n').trim()
  const heading = normalized.match(/^\s*#\s*预览服务修复计划\s*$/m)
  if (!heading || heading.index === undefined) {
    return { activity: [], body: normalized }
  }

  const beforeHeading = normalized.slice(0, heading.index)
  const body = normalized.slice(heading.index + heading[0].length).trim()
  return {
    activity: uniqueActivity(beforeHeading.split(/\n{2,}/)),
    body
  }
}

/** 展示预览服务诊断结果与待确认修复计划，避免普通消息样式稀释操作边界。 */
export default function PreviewRepairPlanCard({
  content
}: PreviewRepairPlanCardProps): ReactElement {
  const [activityExpanded, setActivityExpanded] = useState(false)
  const activityId = useId()
  const sections = planSections(content)
  const latestActivity = sections.activity.at(-1)

  return (
    <article className="preview-repair-plan-card" aria-label="预览服务修复计划">
      <div className="preview-repair-plan-card__header">
        <span className="preview-repair-plan-card__icon" aria-hidden="true">
          <ToolOutlined />
        </span>
        <div className="preview-repair-plan-card__title">
          <span className="preview-repair-plan-card__eyebrow">PREVIEW REPAIR / PLAN 01</span>
          <h3>预览服务修复计划</h3>
        </div>
        <span className="preview-repair-plan-card__status">
          <span /> 待确认
        </span>
      </div>

      <div className="preview-repair-plan-card__meta" aria-hidden="true">
        <span>
          <FileTextOutlined /> DIAGNOSTIC OUTPUT
        </span>
        <span>CODE SCOPE LOCKED</span>
      </div>

      <div className="preview-repair-plan-card__body">
        {latestActivity ? (
          <section className="preview-repair-plan-card__activity" aria-label="执行进度">
            <button
              aria-controls={activityId}
              aria-expanded={activityExpanded}
              className="preview-repair-plan-card__activity-trigger"
              type="button"
              onClick={() => setActivityExpanded((expanded) => !expanded)}
            >
              <span className="preview-repair-plan-card__activity-pulse" aria-hidden="true" />
              <span className="preview-repair-plan-card__activity-label">正在执行</span>
              <span className="preview-repair-plan-card__activity-current">{latestActivity}</span>
              <DownOutlined
                className="preview-repair-plan-card__activity-arrow"
                rotate={activityExpanded ? 180 : 0}
                aria-hidden="true"
              />
            </button>
            {activityExpanded ? (
              <div className="preview-repair-plan-card__activity-history" id={activityId}>
                {sections.activity.map((item, index) => (
                  <div className="preview-repair-plan-card__activity-item" key={`${item}-${index}`}>
                    <span aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
                    <span>{item}</span>
                  </div>
                ))}
              </div>
            ) : null}
          </section>
        ) : null}
        <MarkdownContent content={sections.body} />
      </div>

      <div className="preview-repair-plan-card__footer">
        <SafetyCertificateOutlined />
        <span>计划需确认后执行，修改范围已锁定。</span>
      </div>
    </article>
  )
}
