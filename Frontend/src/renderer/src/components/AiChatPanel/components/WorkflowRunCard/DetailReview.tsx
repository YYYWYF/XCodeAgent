import { Button, Collapse, Input, Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import { useMemo, useState } from 'react'
import type {
  WorkflowDetailReview,
  WorkflowDetailReviewSubmission,
  WorkflowDetailReviewTarget
} from '../../../../typings'
import { cx } from '../../../../utils'

const { Panel } = Collapse
const { Text } = Typography
const { TextArea } = Input

type DetailReviewProps = {
  disabled?: boolean
  onConfirm: (submission: WorkflowDetailReviewSubmission) => void
  review: WorkflowDetailReview
}

export default function DetailReview({ disabled, onConfirm, review }: DetailReviewProps): ReactElement {
  const targets = useMemo(
    () => [...(review.pages || []), ...(review.data_sources || [])],
    [review]
  )
  const [changes, setChanges] = useState<Record<string, Record<string, unknown>>>({})
  const [overallNote, setOverallNote] = useState('')

  const updateField = (target: WorkflowDetailReviewTarget, field: string, value: unknown): void => {
    setChanges((current) => ({
      ...current,
      [target.target_id]: {
        ...(current[target.target_id] || {}),
        [field]: value
      }
    }))
  }

  const confirm = (): void => {
    onConfirm({
      review_status: 'confirmed',
      target_changes: targets
        .filter((target) => Object.keys(changes[target.target_id] || {}).length > 0)
        .map((target) => ({
          target_type: target.target_type,
          target_id: target.target_id,
          changes: changes[target.target_id]
        })),
      overall_note: overallNote.trim() || undefined
    })
  }

  return (
    <div className={cx('workflow-detail-review')}>
      <div className={cx('workflow-detail-review-summary')}>
        <Tag>页面 {review.summary?.page_count || 0}</Tag>
        <Tag>数据源 {review.summary?.data_source_count || 0}</Tag>
        <Tag>API 契约 {review.summary?.api_contract_count || 0}</Tag>
        <Text type="secondary">初版设计已全部生成，只需展开需要调整的对象。</Text>
      </div>
      <Collapse bordered={false}>
        {targets.map((target) => (
          <Panel
            header={
              <div className={cx('workflow-detail-review-title')}>
                <Tag>{target.target_type === 'page' ? '页面' : '数据源'}</Tag>
                <Text strong>{target.name || target.target_id}</Text>
                {changes[target.target_id] && <Tag color="blue">已修改</Tag>}
              </div>
            }
            key={`${target.target_type}:${target.target_id}`}
          >
            {target.target_type === 'page' ? (
              <PageReviewEditor
                changes={changes[target.target_id] || {}}
                disabled={disabled}
                onChange={(field, value) => updateField(target, field, value)}
                target={target}
              />
            ) : (
              <DataSourceReviewEditor
                changes={changes[target.target_id] || {}}
                disabled={disabled}
                onChange={(field, value) => updateField(target, field, value)}
                target={target}
              />
            )}
          </Panel>
        ))}
      </Collapse>
      <TextArea
        autoSize={{ minRows: 2, maxRows: 4 }}
        disabled={disabled}
        onChange={(event) => setOverallNote(event.target.value)}
        placeholder="整体补充说明（可选）"
        value={overallNote}
      />
      <Button disabled={disabled} onClick={confirm} type="primary">
        确认全部设计并继续
      </Button>
    </div>
  )
}

function PageReviewEditor({
  changes,
  disabled,
  onChange,
  target
}: ReviewEditorProps): ReactElement {
  const layout = objectValue(target.basic_layout)
  return (
    <div className={cx('workflow-detail-review-fields')}>
      <ReviewTextField disabled={disabled} label="页面目标" onChange={(value) => onChange('page_goal', value)} value={stringChange(changes.page_goal, target.page_goal)} />
      <ReviewListField disabled={disabled} label="基本布局" onChange={(value) => onChange('basic_layout', { ...layout, structure: value })} value={listChange(objectValue(changes.basic_layout).structure, layout.structure)} />
      <ReadOnlyDetail label="页面布局设计" value={layoutDesignSummary(target.layout_design, target.basic_layout)} />
      <ReviewListField disabled={disabled} label="页面交互" onChange={(value) => onChange('interactions', value)} value={listChange(changes.interactions, target.interactions)} />
      <ReadOnlyDetail label="主要操作交互" value={operationSummary(target.operation_interactions)} />
      <ReadOnlyDetail label="状态反馈" value={stateFeedbackSummary(target.state_feedback)} />
      <ReadOnlyDetail label="API 依赖" value={apiDependencySummary(target.api_dependencies)} />
      <ReadOnlyDetail label="响应字段绑定" value={responseBindingSummary(target.response_bindings)} />
      <ReadOnlyDetail label="页面跳转与依赖" value={navigationSummary(target.page_navigation)} />
      <ReviewListField disabled={disabled} label="页面权限" onChange={(value) => onChange('permissions', value)} value={listChange(changes.permissions, target.permissions)} />
      <ReadOnlyDetail label="操作可见性" value={operationVisibilitySummary(target.operation_visibility)} />
      <ReviewListField disabled={disabled} label="验收标准" onChange={(value) => onChange('acceptance_criteria', value)} value={listChange(changes.acceptance_criteria, target.acceptance_criteria)} />
    </div>
  )
}

function DataSourceReviewEditor({
  changes,
  disabled,
  onChange,
  target
}: ReviewEditorProps): ReactElement {
  return (
    <div className={cx('workflow-detail-review-fields')}>
      <ReadOnlyDetail label="实体与 Schema" value={[...(target.entities || []), ...(target.schema_refs || [])].join('、') || '无'} />
      <ReviewListField disabled={disabled} label="实体关系" onChange={(value) => onChange('relationships', value)} value={listChange(changes.relationships, target.relationships)} />
      <ReviewListField disabled={disabled} label="校验规则" onChange={(value) => onChange('validation_rules', value)} value={listChange(changes.validation_rules, target.validation_rules)} />
      <ReviewTextField disabled={disabled} label="Seed / Mock 策略" onChange={(value) => onChange('seed_strategy', value)} value={stringChange(changes.seed_strategy, target.seed_strategy)} />
      <ReviewListField disabled={disabled} label="验收标准" onChange={(value) => onChange('acceptance_criteria', value)} value={listChange(changes.acceptance_criteria, target.acceptance_criteria)} />
      <ReadOnlyDetail label="API 契约" value={(target.api_contracts || []).map((item) => String(item.id || '')).filter(Boolean).join('、') || '无'} />
    </div>
  )
}

type ReviewEditorProps = {
  changes: Record<string, unknown>
  disabled?: boolean
  onChange: (field: string, value: unknown) => void
  target: WorkflowDetailReviewTarget
}

function ReviewTextField({ disabled, label, onChange, value }: FieldProps<string>): ReactElement {
  return (
    <label className={cx('workflow-detail-review-field')}>
      <Text type="secondary">{label}</Text>
      <TextArea disabled={disabled} onChange={(event) => onChange(event.target.value)} value={value} />
    </label>
  )
}

function ReviewListField({ disabled, label, onChange, value }: FieldProps<string[]>): ReactElement {
  return (
    <label className={cx('workflow-detail-review-field')}>
      <Text type="secondary">{label}</Text>
      <TextArea
        autoSize={{ minRows: 2, maxRows: 5 }}
        disabled={disabled}
        onChange={(event) => onChange(splitLines(event.target.value))}
        value={value.join('\n')}
      />
    </label>
  )
}

function ReadOnlyDetail({ label, value }: { label: string; value: string }): ReactElement {
  return (
    <div className={cx('workflow-detail-review-readonly')}>
      <Text type="secondary">{label}（契约只读）</Text>
      <Text>{value}</Text>
    </div>
  )
}

type FieldProps<T> = {
  disabled?: boolean
  label: string
  onChange: (value: T) => void
  value: T
}

function splitLines(value: string): string[] {
  return value.split(/\n|，|；/).map((item) => item.trim()).filter(Boolean)
}

function listChange(changed: unknown, initial: unknown): string[] {
  return Array.isArray(changed) ? changed.map(String) : Array.isArray(initial) ? initial.map(String) : []
}

function stringChange(changed: unknown, initial: unknown): string {
  return typeof changed === 'string' ? changed : typeof initial === 'string' ? initial : ''
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function recordItems(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === 'object' && !Array.isArray(item))
    : []
}

function stringItems(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String).filter(Boolean) : []
}

function layoutDesignSummary(value: unknown, fallbackLayout: unknown): string {
  const layout = objectValue(value)
  const fallback = objectValue(fallbackLayout)
  const regions = recordItems(layout.regions)
  const regionText = regions.length > 0
    ? regions.map((item) => `${String(item.name || '页面区域')}：${String(item.responsibility || '待补充区域职责')}`).join('\n')
    : stringItems(fallback.structure).map((item) => `${item}：待补充区域职责`).join('\n')
  return [
    `整体布局：${String(layout.overall_layout || '待补充')}`,
    regionText ? `区域划分：\n${regionText}` : '区域划分：待补充',
    `主要内容呈现：${String(layout.primary_content_presentation || '待补充')}`,
    `操作入口位置：${String(layout.operation_entry_position || '待补充')}`,
    `响应式与信息密度：${String(layout.responsive_strategy || fallback.responsive || '待补充')}`
  ].join('\n')
}

function operationSummary(value: unknown): string {
  const lines = recordItems(value).map((item) => {
    const action = String(item.action || item.name || '页面操作')
    const behavior = String(item.behavior || item.description || '待补充行为')
    const endpoint = item.endpoint_id ? `；API ${String(item.endpoint_id)}` : ''
    return `${action}：${behavior}${endpoint}`
  })
  return lines.join('\n') || '无'
}

function stateFeedbackSummary(value: unknown): string {
  const lines = recordItems(value).map((item) => {
    const state = String(item.state || item.name || '反馈状态')
    const behavior = String(item.behavior || item.description || '待补充反馈')
    const scope = String(item.scope || '相关业务区域')
    return `${state}：${scope}；${behavior}`
  })
  return lines.join('\n') || '无'
}

function apiDependencySummary(value: unknown): string {
  const lines = recordItems(value).map((item) => {
    const endpoint = String(item.endpoint_id || 'endpoint')
    const method = String(item.method || 'GET')
    const path = String(item.path || '')
    const usage = String(item.usage || 'read')
    return `${endpoint}：${method} ${path}；${usage}`
  })
  return lines.join('\n') || '无'
}

function responseBindingSummary(value: unknown): string {
  const lines = recordItems(value).map((item) => {
    const endpoint = String(item.endpoint_id || 'endpoint')
    const source = String(item.source_path || '')
    const field = String(item.page_field || source || '页面字段')
    return `${endpoint}：${source} -> ${field}`
  })
  return lines.join('\n') || '无'
}

function navigationSummary(value: unknown): string {
  const lines = recordItems(value).map((item) => {
    const trigger = String(item.trigger || item.action || '页面跳转')
    const target = String(item.target_page_id || item.target_path || '待补充目标页面')
    const behavior = String(item.behavior || item.description || '待补充行为')
    return `${trigger}：${target}；${behavior}`
  })
  return lines.join('\n') || '无'
}

function operationVisibilitySummary(value: unknown): string {
  const lines = recordItems(value).map((item) => {
    const action = String(item.action || '页面操作')
    const visibleTo = stringItems(item.visible_to).join('、') || '待补充'
    const unauthorized = String(item.unauthorized_behavior || '隐藏操作入口或展示无权限提示')
    return `${action}：${visibleTo}；${unauthorized}`
  })
  return lines.join('\n') || '无'
}
