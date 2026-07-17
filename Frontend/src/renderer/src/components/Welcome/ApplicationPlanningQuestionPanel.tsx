import {
  ArrowLeftOutlined,
  BulbOutlined,
  CheckCircleOutlined,
  EyeOutlined,
  FileTextOutlined
} from '@ant-design/icons'
import { Button, Checkbox, Form, Input, Radio, Tag, Typography } from 'antd'
import { useState } from 'react'
import type { ReactElement } from 'react'
import MarkdownContent from '../MarkdownContent/MarkdownContent'
import DetailReview from '../AiChatPanel/components/WorkflowRunCard/DetailReview'
import RequirementSpecSummary from './RequirementSpecSummary'
import type {
  WorkflowClarification,
  WorkflowClarificationAnswer,
  WorkflowClarificationAnswers,
  WorkflowClarificationQuestion,
  WorkflowRunPayload
} from '../../typings'
import { cx } from '../../utils'
import './ApplicationPlanningQuestionPanel.less'

const { Paragraph, Text, Title } = Typography
const { TextArea } = Input
const OTHER_OPTION_VALUE = '__other__'

type Props = {
  disabled?: boolean
  onSubmit: (workflow: WorkflowRunPayload, answers: WorkflowClarificationAnswers) => void
  workflow: WorkflowRunPayload
}

// 从公开 Workflow 载荷中读取当前规划阶段的待确认内容。
function planningClarification(workflow: WorkflowRunPayload): WorkflowClarification | undefined {
  const candidates = [
    workflow.summary.clarification,
    workflow.state?.clarification,
    workflow.result?.clarification
  ]
  return candidates.find(
    (value): value is WorkflowClarification => Boolean(value && typeof value === 'object')
  )
}

// 为规划问题生成稳定表单字段，确保提交答案能与后端问题一一对应。
function questionKey(question: WorkflowClarificationQuestion, index: number): string {
  return question.id || question.header || question.question || `question-${index + 1}`
}

// 将选项答案统一转换为数组，兼容历史字符串答案和新的结构化答案。
function selectedAnswerValues(value?: WorkflowClarificationAnswer): string[] {
  if (typeof value === 'object' && value && !Array.isArray(value) && 'selected' in value) {
    return Array.isArray(value.selected) ? value.selected.map(String) : [String(value.selected)]
  }
  if (Array.isArray(value)) return value.map(String)
  return typeof value === 'string' && value ? [value] : []
}

// 读取“其他”选项对应的补充文本。
function otherAnswerText(value?: WorkflowClarificationAnswer): string {
  return typeof value === 'object' && value && !Array.isArray(value) && 'other' in value
    ? String(value.other || '')
    : ''
}

// 判断回答是否满足当前问题类型的必填约束。
function answerComplete(
  question: WorkflowClarificationQuestion,
  value?: WorkflowClarificationAnswer
): boolean {
  if (question.type === 'choice' || question.type === 'yesno') {
    const selected = selectedAnswerValues(value)
    if (!selected.length) return false
    return !selected.includes(OTHER_OPTION_VALUE) || Boolean(otherAnswerText(value).trim())
  }
  return typeof value === 'string' && Boolean(value.trim())
}

// 根据确认阶段给出符合创建规划语义的标题。
function panelTitle(mode?: string): string {
  if (mode === 'requirement_spec_confirmation') return '确认需求文档'
  if (mode === 'project_plan_confirmation') return '确认项目计划'
  if (mode === 'detail_review') return '审核页面与数据源细节'
  return '补充规划细节'
}

// 根据确认阶段给出下一步按钮文案。
function submitLabel(mode?: string): string {
  if (mode === 'requirement_spec_confirmation') return '确认需求并继续规划'
  if (mode === 'project_plan_confirmation') return '确认计划并进入工作区'
  return '提交回答并继续'
}

// 将 Workflow 的通用提示转换为创建规划页面自己的中文说明。
function panelDescription(clarification: WorkflowClarification): string {
  if (clarification.mode === 'requirement_spec_confirmation') {
    return '请审核需求文档；如有需要调整的内容，可在下方填写修改意见。直接确认即表示文档正确并继续规划。'
  }
  if (clarification.mode === 'project_plan_confirmation') {
    return '请审核当前 ProjectPlan。确认后会立即进入工作区，菜单、API、Schema 和数据源等派生 JSON 将在后续开发规划阶段补齐。'
  }
  const message = String(clarification.message || '')
  if (message && !message.toLowerCase().includes('agent requested user input')) return message
  return '为了让页面和功能规划更贴近真实业务，请补充下面这些关键信息。'
}

// 从公开 Workflow 结果中读取 RequirementSpec 结构化状态，供默认概览视图使用。
function requirementSpec(workflow: WorkflowRunPayload): Record<string, unknown> | undefined {
  for (const source of [workflow.result, workflow.state]) {
    const value = source?.requirement_spec
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      return value as Record<string, unknown>
    }
  }
  return undefined
}

// 使用创建规划页面自己的表单视觉展示问题和正式产物，不渲染通用 Workflow 卡片。
export default function ApplicationPlanningQuestionPanel({
  disabled,
  onSubmit,
  workflow
}: Props): ReactElement | null {
  const [form] = Form.useForm<{ answers: WorkflowClarificationAnswers }>()
  const [showArtifactDetail, setShowArtifactDetail] = useState(false)
  const clarification = planningClarification(workflow)
  const questions = clarification?.questions || []
  const isRequirementConfirmation = clarification?.mode === 'requirement_spec_confirmation'
  const artifact = workflow.confirmationArtifact
  const spec = artifact?.id === 'requirement_spec' ? requirementSpec(workflow) : undefined
  const canShowSummary = Boolean(spec)

  if (!clarification) return null

  // 需求确认只收集可选修改意见；空提交代表用户确认当前文档。
  const handleSubmit = (values: { answers?: WorkflowClarificationAnswers }): void => {
    if (!isRequirementConfirmation) {
      onSubmit(workflow, values.answers || {})
      return
    }
    const feedback = values.answers?.requirement_spec_feedback
    const feedbackText = typeof feedback === 'string' ? feedback.trim() : ''
    onSubmit(workflow, {
      requirement_spec_confirmation: feedbackText
        ? `需要修改：${feedbackText}`
        : '正确，继续规划'
    })
  }

  if (clarification.mode === 'detail_review' && clarification.review) {
    return (
      <section className={cx('planning-question-panel', 'is-detail-review')}>
        <PlanningPanelHeader
          description="逐项检查页面目标、布局、交互、权限、API 和数据源设计，确认后保留在 plans 目录。"
          review
          title={panelTitle(clarification.mode)}
        />
        <DetailReview
          disabled={disabled}
          onConfirm={(submission) => onSubmit(workflow, { detail_review: submission })}
          review={clarification.review}
        />
      </section>
    )
  }

  return (
    <section className={cx('planning-question-panel')}>
      <PlanningPanelHeader
        description={panelDescription(clarification)}
        title={panelTitle(clarification.mode)}
      />

      {artifact?.content ? (
        <section className={cx('planning-artifact-card')}>
          <header>
            <span className={cx('planning-artifact-icon')}><FileTextOutlined /></span>
            <div>
              <Text strong>{artifact.id === 'requirement_spec' ? '需求文档' : 'ProjectPlan'}</Text>
              <Text type="secondary">{artifact.name}</Text>
            </div>
            {canShowSummary ? (
              <Button
                className={cx('planning-artifact-toggle')}
                icon={showArtifactDetail ? <ArrowLeftOutlined /> : <EyeOutlined />}
                onClick={() => setShowArtifactDetail((current) => !current)}
                size="small"
                type="text"
              >
                {showArtifactDetail ? '返回概览' : '查看详情'}
              </Button>
            ) : <Tag>Markdown</Tag>}
          </header>
          <div className={cx('planning-artifact-content')}>
            {canShowSummary && !showArtifactDetail
              ? <RequirementSpecSummary spec={spec!} />
              : <MarkdownContent content={artifact.content} />}
          </div>
        </section>
      ) : null}

      <Form
        className={cx('planning-question-form')}
        form={form}
        layout="vertical"
        onFinish={handleSubmit}
      >
        {isRequirementConfirmation ? (
          <section className={cx('planning-question-card')}>
            <div className={cx('planning-question-card-heading')}>
              <div className={cx('planning-question-title')}>
                <Title level={5}>修改意见（可选）</Title>
              </div>
              <Paragraph type="secondary">留空并确认，即表示需求文档正确并继续项目规划。</Paragraph>
            </div>
            <Form.Item name={['answers', 'requirement_spec_feedback']}>
              <TextArea
                autoSize={{ minRows: 3, maxRows: 7 }}
                disabled={disabled}
                placeholder="例如：增加审批人角色，并补充审批页面。"
              />
            </Form.Item>
          </section>
        ) : questions.map((question, index) => {
          const key = questionKey(question, index)
          return (
            <section className={cx('planning-question-card')} key={key}>
              <div className={cx('planning-question-card-heading')}>
                <div>
                  <div className={cx('planning-question-title')}>
                    {question.header || question.dimension ? (
                      <Tag>{question.header || question.dimension}</Tag>
                    ) : null}
                    <span aria-hidden className={cx('planning-question-required')}>*</span>
                    <Title level={5}>{question.question || '请补充规划细节'}</Title>
                  </div>
                  {question.default_assumption ? (
                    <Paragraph type="secondary">默认建议：{question.default_assumption}</Paragraph>
                  ) : null}
                </div>
              </div>
              <Form.Item
                name={['answers', key]}
                required
                rules={[{
                  validator: (_rule, value) => answerComplete(question, value)
                    ? Promise.resolve()
                    : Promise.reject(new Error('请选择或补充这个问题'))
                }]}
              >
                <PlanningQuestionControl
                  disabled={disabled}
                  question={question}
                />
              </Form.Item>
            </section>
          )
        })}

        {(isRequirementConfirmation || questions.length) ? (
          <div className={cx('page-planning-actions')}>
            <Button
              disabled={disabled}
              htmlType="submit"
              icon={clarification.mode?.includes('confirmation') ? <CheckCircleOutlined /> : <BulbOutlined />}
              type="primary"
            >
              {submitLabel(clarification.mode)}
            </Button>
          </div>
        ) : null}
      </Form>
    </section>
  )
}

// 根据模型返回的结构化问题，分别渲染单选、多选、是非题或自由文本。
function PlanningQuestionControl({
  disabled,
  onChange,
  question,
  value
}: {
  disabled?: boolean
  onChange?: (value: WorkflowClarificationAnswer) => void
  question: WorkflowClarificationQuestion
  value?: WorkflowClarificationAnswer
}): ReactElement {
  const options = question.type === 'yesno'
    ? [
        { label: '是', description: '', value: '是' },
        { label: '否', description: '', value: '否' }
      ]
    : (question.options || [])
        .filter((option) => option.label)
        .map((option) => ({
          label: option.label || '',
          description: option.description || '',
          value: option.value || option.label || ''
        }))
  const optionsWithOther = question.allowOther !== false
    && !options.some((option) => option.value === OTHER_OPTION_VALUE)
    ? [...options, { label: '其他', description: '补充未覆盖的需求或偏好。', value: OTHER_OPTION_VALUE }]
    : options
  const selected = selectedAnswerValues(value)
  const other = otherAnswerText(value)
  const emitSelection = (nextSelected: string[]): void => {
    onChange?.({ selected: nextSelected, other: other || undefined })
  }
  const emitOther = (nextOther: string): void => {
    onChange?.({ selected, other: nextOther })
  }

  if ((question.type === 'choice' || question.type === 'yesno') && optionsWithOther.length) {
    return (
      <div className={cx('planning-question-control')}>
        {question.type === 'choice' && question.multiSelect ? (
          <Checkbox.Group
            className={cx('planning-question-options')}
            disabled={disabled}
            onChange={(checkedValues) => emitSelection(checkedValues.map(String))}
            value={selected}
          >
            {optionsWithOther.map((option) => (
              <Checkbox className={cx('planning-question-option')} key={option.value} value={option.value}>
                <OptionCopy description={option.description} label={option.label} />
              </Checkbox>
            ))}
          </Checkbox.Group>
        ) : (
          <Radio.Group
            className={cx('planning-question-options')}
            disabled={disabled}
            onChange={(event) => emitSelection([String(event.target.value)])}
            value={selected[0]}
          >
            {optionsWithOther.map((option) => (
              <Radio className={cx('planning-question-option')} key={option.value} value={option.value}>
                <OptionCopy description={option.description} label={option.label} />
              </Radio>
            ))}
          </Radio.Group>
        )}
        {selected.includes(OTHER_OPTION_VALUE) ? (
          <TextArea
            autoSize={{ minRows: 2, maxRows: 5 }}
            disabled={disabled}
            onChange={(event) => emitOther(event.target.value)}
            placeholder="请补充其他选择或说明"
            value={other}
          />
        ) : null}
      </div>
    )
  }

  return (
    <TextArea
      autoSize={{ minRows: 3, maxRows: 7 }}
      disabled={disabled}
      onChange={(event) => onChange?.(event.target.value)}
      placeholder={question.placeholder || '请输入你的回答，也可以直接说明希望采用的方案。'}
      value={typeof value === 'string' ? value : ''}
    />
  )
}

// 在选项控件中同时展示简短标签和模型提供的解释。
function OptionCopy({ description, label }: { description: string; label: string }): ReactElement {
  return (
    <span className={cx('planning-question-option-copy')}>
      <Text strong>{label}</Text>
      {description ? <Text type="secondary">{description}</Text> : null}
    </span>
  )
}

// 渲染创建规划专用的标题说明区。
function PlanningPanelHeader({
  description,
  review,
  title
}: {
  description: string
  review?: boolean
  title: string
}): ReactElement {
  return (
    <header className={cx('planning-question-panel-header')}>
      <span className={cx('planning-question-panel-icon')}>
        {review ? <CheckCircleOutlined /> : <BulbOutlined />}
      </span>
      <div>
        <Title level={4}>{title}</Title>
        <Paragraph type="secondary">{description}</Paragraph>
      </div>
    </header>
  )
}
