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
      <ReviewListField disabled={disabled} label="页面交互" onChange={(value) => onChange('interactions', value)} value={listChange(changes.interactions, target.interactions)} />
      <ReviewListField disabled={disabled} label="页面权限" onChange={(value) => onChange('permissions', value)} value={listChange(changes.permissions, target.permissions)} />
      <ReviewListField disabled={disabled} label="验收标准" onChange={(value) => onChange('acceptance_criteria', value)} value={listChange(changes.acceptance_criteria, target.acceptance_criteria)} />
      <ReadOnlyDetail label="API 与数据依赖" value={dependencySummary(target)} />
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

function dependencySummary(target: WorkflowDetailReviewTarget): string {
  const dependencies = objectValue(target.page_dependencies)
  const apiContracts = Array.isArray(dependencies.api_contracts) ? dependencies.api_contracts.map(String) : []
  const dataSources = Array.isArray(dependencies.data_sources) ? dependencies.data_sources.map(String) : []
  return [`数据源：${dataSources.join('、') || '无'}`, `API：${apiContracts.join('、') || '无'}`].join('；')
}
