import { Input, Select, Tag, TreeSelect } from 'antd'
import { useRef, useState, type ReactElement } from 'react'
import type { WorkflowApiBusinessValue, WorkflowApiField, WorkflowApiValueRight } from '../../../../typings'
import { apiDesignFieldKey, endpointFieldSnapshot } from '../WorkflowRunCard/apiDesignSerialization'
import RuleEditor, { type RuleContent } from './RuleEditor'
import BusinessRuleEntry from './BusinessRuleEntry'
import ReadOnlyValue from './ReadOnlyValue'
import { BUILTIN_OPTIONS, VALUE_SOURCE_OPTIONS, businessValue, parseValue, valueText } from './valueRules'

type Props = { right?: WorkflowApiValueRight; fields: WorkflowApiField[]; type: string; title: string; readOnly: boolean; disabled: boolean; collection?: boolean; allowEndpoint?: boolean; onSource?: () => void; onBusiness?: () => void; onCancelPending?: () => void; onChange: (right?: WorkflowApiValueRight) => void }

const PARAMETER_LOCATION_LABELS = { path: '路径参数', query: '查询参数', header: '请求头', request_body: '请求体', response_body: '响应体' }

/** 将参数树挂到页面浮层，避免行内滚动容器裁剪和挤压弹层定位。 */
function parameterPopupContainer(trigger: HTMLElement): HTMLElement {
  return trigger.ownerDocument.body
}

/** 为查询、写入、外部入参和业务返回值提供统一取值与行内处理控件。 */
export default function ValueRuleEditor({ right, fields, type, title, readOnly, disabled, collection, allowEndpoint = true, onSource, onBusiness, onCancelPending, onChange }: Props): ReactElement {
  const [pending, setPending] = useState<WorkflowApiBusinessValue | undefined>(() => right?.kind === 'business' && !right.businessDescription ? businessValue(right) : undefined)
  const [fixedInput, setFixedInput] = useState<{ value: unknown; text: string }>()
  const parameterAnchor = useRef<HTMLDivElement>(null)
  const [popupLayout, setPopupLayout] = useState<{ placement: 'bottomLeft' | 'topLeft'; height: number }>({ placement: 'bottomLeft', height: 256 })
  /** 根据可用空间选择展开方向，用列表滚动代替弹层向输入框挤压。 */
  const updateParameterPopup = (open: boolean): void => {
    if (!open || !parameterAnchor.current) return
    const anchor = parameterAnchor.current
    const rect = anchor.getBoundingClientRect()
    const viewportHeight = anchor.ownerDocument.documentElement.clientHeight
    const below = viewportHeight - rect.bottom - 16
    const above = rect.top - 16
    const useBelow = below >= 256 || below >= above
    setPopupLayout({ placement: useBelow ? 'bottomLeft' : 'topLeft', height: Math.max(24, Math.min(256, (useBelow ? below : above) - 8)) })
  }

  const requestFields = fields.filter((field) => field.side === 'request')
  const kind = pending ? 'business' : right?.kind || 'endpoint'
  const options = requestFields.map((field) => ({ value: apiDesignFieldKey(field), label: `${field.path} · ${field.type}`, group: PARAMETER_LOCATION_LABELS[field.location] }))
  // 类别节点只负责组织字段；叶子保留位置身份，避免同名参数绑定混淆。
  const parameterTree = (['path', 'query', 'header', 'request_body'] as const).map((location) => ({
    value: `category:${location}`,
    title: PARAMETER_LOCATION_LABELS[location],
    selectable: false,
    children: requestFields.filter((field) => field.location === location).map((field) => ({
      value: apiDesignFieldKey(field),
      searchText: `${field.path} ${field.type} ${field.location} ${PARAMETER_LOCATION_LABELS[field.location]}`,
      title: `${field.path} · ${field.type}`,
      label: <span className="field-parameter-option"><span className="field-parameter-name">{field.path} · {field.type}</span><Tag>{PARAMETER_LOCATION_LABELS[field.location]}</Tag></span>
    }))
  })).filter((category) => category.children.length > 0)
  const rule = pending || (right?.kind === 'business' ? right : undefined)
  /** 将规则表单中的依赖身份恢复为请求快照和内置键。 */
  const apply = (value: RuleContent): void => {
    if (!pending) return
    const endpointFields = requestFields.filter((field) => value.dependencies.includes(apiDesignFieldKey(field))).map(endpointFieldSnapshot)
    const builtinFields = BUILTIN_OPTIONS.filter((item) => value.dependencies.includes(`builtin:${item.value}`)).map((item) => item.value as WorkflowApiBusinessValue['builtinFields'][number])
    onChange({ kind: 'business', origin: 'business', endpointFields, builtinFields, businessDescription: value.description, missingBehavior: value.missing, ...(value.missing === 'default' ? { defaultValue: value.defaultValue } : {}) })
    setPending(undefined)
  }
  /** 切换来源时先编辑复杂规则，取消可恢复原值。 */
  const changeKind = (next: string): void => {
    if (next === 'source') { setPending(undefined); onSource?.(); return }
    if (next === 'builtin') return
    if (next === 'business') { if (onBusiness) onBusiness(); else setPending(businessValue(right)); return }
    setPending(undefined)
    onChange(next === 'fixed' ? { kind: 'fixed' } : { kind: 'endpoint' })
  }
  if (readOnly) return <ReadOnlyValue title={title} right={right} />
  return <div className="field-value-control">
    <div className="field-value-inputs">
      <Select aria-label={`${title}值来源`} disabled={disabled} value={kind} options={[...(onSource ? [{ value: 'source', label: '数据源字段' }] : []), ...VALUE_SOURCE_OPTIONS.filter((item) => allowEndpoint || item.value !== 'endpoint')]} onChange={changeKind} />
      {kind === 'fixed' ? /bool|^bit/i.test(type) && !collection ? <Select aria-label={`${title}固定值`} disabled={disabled} placeholder="选择固定值" value={right?.kind === 'fixed' ? right.value === undefined ? undefined : String(right.value) : undefined} options={[{ value: 'true', label: 'true' }, { value: 'false', label: 'false' }]} onChange={(value) => onChange({ kind: 'fixed', value: value === 'true' })} />
        : <Input aria-label={`${title}固定值`} disabled={disabled} value={right?.kind === 'fixed' ? fixedInput && Object.is(fixedInput.value, right.value) ? fixedInput.text : valueText(right.value) : ''} placeholder={collection ? 'JSON 数组，例如 [1, 10]' : '输入固定值'} onChange={(event) => { const text = event.target.value; const value = parseValue(text, type, collection); setFixedInput({ value, text }); onChange({ kind: 'fixed', value }) }} />
        : rule ? <BusinessRuleEntry title={title} configured={Boolean(right?.kind === 'business' && right.businessDescription)}
          inputCount={rule.endpointFields.length + rule.builtinFields.length} disabled={disabled}
          onClick={() => { if (onBusiness) onBusiness(); else setPending(businessValue(right)) }} />
          : <div ref={parameterAnchor} className="field-parameter-anchor"><TreeSelect<string> allowClear showSearch treeDefaultExpandAll treeLine treeNodeFilterProp="searchText" treeNodeLabelProp="label" popupClassName="field-parameter-tree" placement={popupLayout.placement} listHeight={popupLayout.height} dropdownAlign={{ overflow: { adjustX: true, adjustY: false } }} onDropdownVisibleChange={updateParameterPopup} getPopupContainer={parameterPopupContainer} aria-label={`${title}接口参数`} disabled={disabled} placeholder="选择接口参数" value={right?.kind === 'endpoint' && right.endpointField ? apiDesignFieldKey(right.endpointField) : undefined} treeData={parameterTree} onChange={(key) => { const field = requestFields.find((item) => apiDesignFieldKey(item) === key); onChange({ kind: 'endpoint', ...(field ? { endpointField: endpointFieldSnapshot(field) } : {}) }) }} /></div>}
    </div>
    {pending && !readOnly ? <RuleEditor key={pending.origin} title={`业务规则 · ${title}`} initial={{ dependencies: [...pending.endpointFields.map(apiDesignFieldKey), ...pending.builtinFields.map((key) => `builtin:${key}`)], description: pending.businessDescription, missing: pending.missingBehavior, defaultValue: pending.defaultValue }}
      options={[...options, ...BUILTIN_OPTIONS.map((item) => ({ ...item, value: `builtin:${item.value}` }))]} type={type} collection={collection} disabled={disabled} canDirect={pending.endpointFields.length === 1 && !pending.builtinFields.length}
      onDirect={() => { onChange({ kind: 'endpoint', endpointField: pending.endpointFields[0] }); setPending(undefined) }} onClose={() => { setPending(undefined); onCancelPending?.() }} onApply={apply} /> : null}
  </div>
}
