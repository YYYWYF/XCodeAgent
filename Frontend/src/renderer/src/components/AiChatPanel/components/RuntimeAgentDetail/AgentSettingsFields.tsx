import { Form, Input, InputNumber, Select } from 'antd'
import type { ReactElement } from 'react'
import type { EditableAgentSettings } from '../../../../service/runtimeWorkbench'

/** 规范化五个编辑字段；人设全部留空表示不启用独立人设。 */
export function normalizedSettings(value: EditableAgentSettings): EditableAgentSettings {
  const role = value.prompt.persona?.role.trim() || ''
  const tone = value.prompt.persona?.tone.trim() || ''
  return { prompt: { systemPrompt: value.prompt.systemPrompt.trim(),
    ...(role || tone ? { persona: { role, tone } } : {}),
    constraints: (value.prompt.constraints || []).map((line) => line.trim()).filter(Boolean) },
    temperature: value.temperature }
}

/** 沿原设置编辑布局展示一份表单，模型身份及凭据继续沿用项目配置。 */
export default function AgentSettingsFields({ value, disabled, onChange }: {
  value: EditableAgentSettings; disabled: boolean; onChange: (value: EditableAgentSettings) => void
}): ReactElement {
  /** 更新同一份 Prompt 表单值，保留其他已编辑的字段。 */
  function promptField(field: Partial<EditableAgentSettings['prompt']>): void {
    onChange({ ...value, prompt: { ...value.prompt, ...field } })
  }
  return <Form component="div" layout="vertical" className="agent-settings-fields">
    <div className="agent-settings-form-grid">
      <Form.Item label="角色"><Input aria-label="Agent 角色" maxLength={2000} disabled={disabled}
        value={value.prompt.persona?.role || ''} onChange={(event) => promptField({ persona: {
          role: event.target.value, tone: value.prompt.persona?.tone || '' } })} /></Form.Item>
      <Form.Item label="表达风格"><Input aria-label="Agent 表达风格" maxLength={2000} disabled={disabled}
        value={value.prompt.persona?.tone || ''} onChange={(event) => promptField({ persona: {
          role: value.prompt.persona?.role || '', tone: event.target.value } })} /></Form.Item>
    </div>
    <Form.Item label="System Prompt"><Input.TextArea aria-label="Agent 系统提示词" autoSize={{ minRows: 6, maxRows: 16 }} maxLength={100000} showCount
      disabled={disabled} value={value.prompt.systemPrompt} onChange={(event) => promptField({ systemPrompt: event.target.value })} /></Form.Item>
    <Form.Item label="业务约束" extra="每行一条业务约束"><Input.TextArea aria-label="Agent 业务约束" autoSize={{ minRows: 3, maxRows: 8 }} placeholder="每行一条业务约束"
      disabled={disabled} value={(value.prompt.constraints || []).join('\n')}
      onChange={(event) => promptField({ constraints: event.target.value.split('\n') })} /></Form.Item>
    <div className="agent-settings-form-grid">
      <Form.Item label="模型策略"><Select aria-label="Agent 模型策略" disabled value="project"
        options={[{ label: '跟随项目默认模型', value: 'project' }]} /></Form.Item>
      <Form.Item label="Temperature" extra="范围 0～2，清空后跟随项目默认。只影响当前智能体。">
        <InputNumber<number> aria-label="Agent Temperature" min={0} max={2} step={0.1} precision={2}
          disabled={disabled} placeholder="跟随项目默认" value={value.temperature}
          onChange={(temperature) => onChange({ ...value, temperature })} />
      </Form.Item>
    </div>
  </Form>
}
