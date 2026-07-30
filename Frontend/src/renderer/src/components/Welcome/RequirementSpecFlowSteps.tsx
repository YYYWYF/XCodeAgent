import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import { Button, Input, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../utils'

const { Text } = Typography
const { TextArea } = Input

type FlowStepProps = {
  flowIndex: number
  steps: unknown
  onChange: (flowIndex: number, steps: string[]) => void
}

type FlowEditorProps = {
  flowIndex: number
  item: Record<string, unknown>
  onRemove: (index: number) => void
  onUpdate: (index: number, key: string, value: unknown) => void
}

// 将流程步骤转换为可编辑字符串列表，并兼容旧模型可能返回的对象步骤。
function stepList(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  const normalized = value.map((item) => {
    if (typeof item === 'string') return item
    if (item && typeof item === 'object') {
      const record = item as Record<string, unknown>
      return String(record.description || record.name || record.title || '')
    }
    return String(item ?? '')
  })
  return normalized
}

// 把字段值转换为表单需要的文本。
function textValue(value: unknown): string {
  return typeof value === 'string' ? value : value == null ? '' : String(value)
}

// 渲染可增删改的业务流程步骤编辑控件。
function RequirementSpecFlowSteps({ flowIndex, onChange, steps }: FlowStepProps): ReactElement {
  const editableSteps = stepList(steps)

  // 更新单个步骤的文本，并保留当前位置。
  const updateStep = (stepIndex: number, value: string): void => {
    onChange(
      flowIndex,
      editableSteps.map((step, index) => (index === stepIndex ? value : step))
    )
  }

  // 在末尾追加一个空步骤，方便用户继续拆分流程。
  const addStep = (): void => {
    onChange(flowIndex, [...editableSteps, ''])
  }

  // 删除指定步骤，允许用户把步骤列表清空后再按需添加。
  const removeStep = (stepIndex: number): void => {
    onChange(
      flowIndex,
      editableSteps.filter((_step, index) => index !== stepIndex)
    )
  }

  return (
    <div className={cx('requirement-editor-flow-steps')}>
      <div className={cx('requirement-editor-flow-steps-heading')}>
        <Text>流程步骤</Text>
        <Button icon={<PlusOutlined />} onClick={addStep} size="small" type="text">
          添加步骤
        </Button>
      </div>
      <div className={cx('requirement-editor-flow-step-list')}>
        {editableSteps.map((step, stepIndex) => (
          <div
            className={cx('requirement-editor-flow-step')}
            key={`flow-${flowIndex}-step-${stepIndex}`}
          >
            <span>{stepIndex + 1}</span>
            <Input
              onChange={(event) => updateStep(stepIndex, event.target.value)}
              placeholder={`请输入第 ${stepIndex + 1} 步`}
              value={step}
            />
            <Button
              aria-label={`删除第 ${stepIndex + 1} 步`}
              icon={<DeleteOutlined />}
              onClick={() => removeStep(stepIndex)}
              size="small"
              type="text"
            />
          </div>
        ))}
      </div>
    </div>
  )
}

// 渲染单个业务流程编辑项，把说明和步骤保持在同一个结构化入口内。
export default function RequirementSpecFlowEditor({
  flowIndex,
  item,
  onRemove,
  onUpdate
}: FlowEditorProps): ReactElement {
  return (
    <article className={cx('requirement-editor-item')}>
      <Button
        aria-label="删除该需求项"
        className={cx('requirement-editor-remove')}
        icon={<DeleteOutlined />}
        onClick={() => onRemove(flowIndex)}
        size="small"
        type="text"
      />
      <div className={cx('requirement-editor-field')}>
        <Text>流程名称</Text>
        <Input
          onChange={(event) => onUpdate(flowIndex, 'name', event.target.value)}
          placeholder="请输入流程名称"
          value={textValue(item.name)}
        />
      </div>
      <div className={cx('requirement-editor-field')}>
        <Text>流程说明</Text>
        <TextArea
          autoSize={{ minRows: 2, maxRows: 4 }}
          onChange={(event) => onUpdate(flowIndex, 'description', event.target.value)}
          placeholder="请输入流程说明"
          value={textValue(item.description)}
        />
      </div>
      <RequirementSpecFlowSteps
        flowIndex={flowIndex}
        onChange={(index, steps) => onUpdate(index, 'steps', steps)}
        steps={item.steps}
      />
    </article>
  )
}
