import { Button, Input, Modal, Select } from 'antd'
import { createContext, useContext, useEffect, useRef, useState, type ReactElement } from 'react'
import './ValueRuleEditor.less'

export const RuleEditingContext = createContext<(delta: number) => void>(() => {})
export type RuleContent = { dependencies: string[]; description: string; missing: 'error' | 'omit' | 'default'; defaultValue?: unknown }
type InputSource = 'source' | 'endpoint' | 'builtin'
type InputRow = { id: number; source: InputSource; field?: string }
type Props = { title: string; initial: RuleContent; options: Array<{ value: string; label: string; group?: string }>; type: string; collection?: boolean; disabled: boolean; canDirect?: boolean; purposeOnly?: boolean; onDirect?: () => void; onApply: (value: RuleContent) => void; onClose: () => void }

/** 从依赖身份区分数据源、接口参数与内置上下文，不修改持久化字段键。 */
function inputSource(key: string): InputSource {
  return key.startsWith('source:') ? 'source' : key.startsWith('builtin:') ? 'builtin' : 'endpoint'
}

/** 在独立弹窗编辑规则，显式应用前不覆盖原映射，并阻止外层确认旧值。 */
export default function RuleEditor({ title, initial, options, type, disabled, purposeOnly, onApply, onClose }: Props): ReactElement {
  const [value, setValue] = useState(initial)
  const [rows, setRows] = useState<InputRow[]>(() => initial.dependencies.map((field, id) => ({ id, source: inputSource(field), field })))
  const nextId = useRef(initial.dependencies.length)
  const [error, setError] = useState('')
  const editing = useContext(RuleEditingContext)
  useEffect(() => { editing(1); return () => editing(-1) }, [editing])
  // 候选字段由调用场景限定；空类别不进入菜单，内置参数仅保留未开放提示。
  const sourceOptions = [
    { value: 'source', label: '数据源字段' },
    { value: 'endpoint', label: '接口参数' }
  ].filter((source) => options.some((option) => inputSource(option.value) === source.value))
  const selectableSources = [...sourceOptions, { value: 'builtin', label: '内置参数', disabled: true }]
  const nextOption = options.find((option) => inputSource(option.value) !== 'builtin' && !rows.some((row) => row.field === option.value))
  /** 按行验证输入后提交完整依赖，保留暂不开放编辑的缺值策略。 */
  const apply = (): void => {
    if (!value.description.trim()) { setError('请填写明确的处理规则。'); return }
    if (rows.some((row) => !row.field || !options.some((option) => option.value === row.field && inputSource(option.value) === row.source))) { setError('请为每项输入选择有效字段，或删除不需要的输入。'); return }
    const dependencies = rows.map((row) => row.field!)
    if (new Set(dependencies).size !== dependencies.length) { setError('同一字段无需重复添加。'); return }
    onApply({ ...value, dependencies, description: value.description.trim() })
  }
  /** 修改单行并清除已过期的校验提示。 */
  const updateRow = (id: number, patch: Partial<InputRow>): void => {
    setRows((current) => current.map((row) => row.id === id ? { ...row, ...patch } : row))
    setError('')
  }
  /** 在状态更新前分配稳定行号，避免重复执行更新函数消耗行号。 */
  const addInput = (): void => {
    if (!nextOption) return
    const row: InputRow = { id: nextId.current++, source: inputSource(nextOption.value) }
    setRows((current) => [...current, row])
    setError('')
  }
  /** 仅展示所选值来源的字段，按参数位置分组并禁用其他行已选字段。 */
  const fieldOptions = (row: InputRow) => {
    const groups = new Map<string, Array<{ value: string; label: string; disabled: boolean }>>()
    options.filter((option) => inputSource(option.value) === row.source).forEach((option) => {
      const group = option.group || (row.source === 'source' ? '当前数据源' : row.source === 'builtin' ? '内置上下文' : '接口参数')
      const items = groups.get(group) || []
      items.push({ value: option.value, label: option.label, disabled: rows.some((other) => other.id !== row.id && other.field === option.value) })
      groups.set(group, items)
    })
    return Array.from(groups, ([label, items]) => ({ label, options: items }))
  }
  return <Modal open width={760} className="field-rule-modal" maskClosable={false} closable={!disabled} keyboard={!disabled} onCancel={onClose}
    title={<div className="field-rule-dialog-heading"><strong>{purposeOnly ? '编辑业务用途' : '编辑业务规则'}</strong><div>{title} · {type}</div></div>}
    footer={<div className="field-rule-dialog-actions">{error ? <span className="field-rule-error" role="alert">{error}</span> : null}<Button disabled={disabled} onClick={onClose}>取消</Button><Button type="primary" disabled={disabled} onClick={apply}>应用规则</Button></div>}>
    <div className="field-rule-editor">
    {!purposeOnly ? <div className="field-rule-input-section">
      <div className="field-rule-input-heading"><strong>输入字段 <small>{rows.length} 项</small></strong><Button type="link" className="field-rule-link" disabled={disabled || !nextOption} onClick={addInput}>＋ 添加输入</Button></div>
      <div className="field-rule-input-grid field-rule-input-head"><span>值来源</span><span>字段</span><span /></div>
      {rows.map((row, index) => <div key={row.id} className="field-rule-input-grid field-rule-input-row">
        <Select aria-label={`输入 ${index + 1} 值来源`} disabled={disabled} value={selectableSources.some((source) => source.value === row.source) ? row.source : undefined} placeholder="请选择有效来源" options={selectableSources} onChange={(source: InputSource) => updateRow(row.id, { source, field: undefined })} />
        <Select className="field-rule-input-field" aria-label={`输入 ${index + 1} 字段`} allowClear showSearch optionFilterProp="label" disabled={disabled || row.source === 'builtin'} value={row.field} options={fieldOptions(row)} placeholder="选择字段" onChange={(field) => updateRow(row.id, { field })} />
        <Button className="field-rule-input-remove" type="text" disabled={disabled} aria-label={`删除输入 ${index + 1}`} onClick={() => { setRows((current) => current.filter((item) => item.id !== row.id)); setError('') }}>×</Button>
      </div>)}
      {!rows.length ? <span className="field-rule-empty">无输入，按业务规则独立生成</span> : null}
    </div> : null}
    <label>处理规则<Input.TextArea disabled={disabled} value={value.description} maxLength={2000} autoSize={{ minRows: 5, maxRows: 10 }} placeholder="说明如何根据输入字段得到目标值" onChange={(event) => { setValue({ ...value, description: event.target.value }); setError('') }} /></label>
    </div>
  </Modal>
}
