import { Select } from 'antd'
import { useState, type ReactElement } from 'react'
import type { WorkflowApiDesignDraft, WorkflowApiField, WorkflowApiSourceField } from '../../../../typings'
import { apiDesignFieldKey, endpointFieldSnapshot, replaceFieldMapping } from '../WorkflowRunCard/apiDesignSerialization'
import { sourceFieldKey } from './model'
import RuleEditor, { type RuleContent } from './RuleEditor'
import BusinessRuleEntry from './BusinessRuleEntry'
import ValueRuleEditor from './ValueRuleEditor'
import ReadOnlyValue from './ReadOnlyValue'
import { BUILTIN_OPTIONS, VALUE_SOURCE_OPTIONS } from './valueRules'

type Props = { field: WorkflowApiField; fields: WorkflowApiField[]; sources: WorkflowApiSourceField[]; draft: WorkflowApiDesignDraft; readOnly: boolean; disabled: boolean; error?: string; onChange: (draft: WorkflowApiDesignDraft) => void }

/** 为两种数据来源提供统一表头，编辑态沿用映射行的嵌套列宽。 */
export function ResponseFieldMappingHeader({ readOnly }: { readOnly: boolean }): ReactElement {
  if (readOnly) return <div className="field-detail-row-head"><span>返回字段</span><span>值来源</span><span>取值内容</span></div>
  return <div className="field-response-row field-response-edit-head">
    <span>返回字段</span><span aria-hidden="true" />
    <div className="field-value-control"><div className="field-value-inputs"><span>值来源</span><span>取值内容</span></div></div>
  </div>
}

/** 按目标返回字段展示直接来源、多字段加工或无来源业务生成。 */
export default function ResponseFieldMapping({ field, fields, sources, draft, readOnly, disabled, error, onChange }: Props): ReactElement {
  const [editing, setEditing] = useState(false)
  const mapping = draft.fieldMappings.find((item) => apiDesignFieldKey(item.endpointField) === apiDesignFieldKey(field))
  const mapped = mapping?.mappingType === 'source_mapping' ? mapping : undefined
  const valueMapping = mapping?.mappingType === 'value_mapping' ? mapping : undefined
  const business = mapping?.mappingType === 'business_description'
  const valueRule = valueMapping?.right.kind === 'business' ? valueMapping.right : undefined
  const processed = !!valueRule || business || !!mapped && mapped.processingType !== 'direct'
  const sourceMode = editing || !valueMapping || !!valueRule
  const ruleInputs = valueRule || mapped
  const ruleDescription = ruleInputs?.businessDescription || (business ? mapping.businessDescription : '')
  const requestDependencies = ruleInputs?.endpointFields || (valueMapping?.right.kind === 'endpoint' && valueMapping.right.endpointField ? [valueMapping.right.endpointField] : [])
  const endpointField = endpointFieldSnapshot(field)
  const selected = mapped?.sourceFields || []
  const options = sources.map((source) => ({ value: sourceFieldKey(source), label: `${source.sourceType === 'database' ? source.column : source.path}${source.description ? `（${source.description}）` : ''} · ${source.type}` }))
  const issue = error || (selected.some((source) => !sources.some((candidate) => sourceFieldKey(candidate) === sourceFieldKey(source))) ? '来源字段已失效，请重新配置。' : '')
  const ruleOptions = [...options.map((item) => ({ ...item, value: `source:${item.value}` })),
    ...fields.filter((item) => item.side === 'request').map((item) => ({ value: `endpoint:${apiDesignFieldKey(item)}`, label: `${item.path} · ${item.type}`, group: ({ path: '路径参数', query: '查询参数', header: '请求头', request_body: '请求体', response_body: '响应体' })[item.location] })),
    ...BUILTIN_OPTIONS.map((item) => ({ ...item, value: `builtin:${item.value}` }))]
  /** 将多字段规则应用为已有的来源映射契约。 */
  const apply = (rule: RuleContent): void => {
    const sourceFields = sources.filter((source) => rule.dependencies.includes(`source:${sourceFieldKey(source)}`))
    const endpointFields = fields.filter((item) => item.side === 'request' && rule.dependencies.includes(`endpoint:${apiDesignFieldKey(item)}`)).map(endpointFieldSnapshot)
    const builtinFields = BUILTIN_OPTIONS.filter((item) => rule.dependencies.includes(`builtin:${item.value}`)).map((item) => item.value as 'current_user_id' | 'current_time')
    if (!sourceFields.length) {
      onChange(replaceFieldMapping(draft, { endpointField, mappingType: 'value_mapping', right: { kind: 'business', origin: 'business', endpointFields, builtinFields, businessDescription: rule.description, missingBehavior: rule.missing, ...(rule.missing === 'default' ? { defaultValue: rule.defaultValue } : {}) } }))
    } else onChange(replaceFieldMapping(draft, { endpointField, mappingType: 'source_mapping', sourceFields, endpointFields, builtinFields, processingType: sourceFields.length > 1 ? 'multi_field_description' : 'single_field_description', businessDescription: rule.description, missingBehavior: rule.missing, ...(rule.missing === 'default' ? { defaultValue: rule.defaultValue } : {}) }))
    setEditing(false)
  }
  /** 恢复单来源直接映射时只保留明确选择的一个来源。 */
  const direct = (key?: string): void => {
    const source = sources.find((item) => sourceFieldKey(item) === key)
    onChange(replaceFieldMapping(draft, source ? { endpointField, mappingType: 'source_mapping', processingType: 'direct', sourceFields: [source] } : { endpointField, mappingType: 'unconfigured' }))
    setEditing(false)
  }
  if (readOnly) return <div className="field-response-row is-readonly">
    <div className="field-response-target" title={field.path}>
      <code>{field.path}</code><span className="field-response-type">{field.type}</span>
    </div>
    <ReadOnlyValue title={field.path} right={valueMapping?.right} sourceFields={selected} businessDescription={ruleDescription}
      endpointFields={requestDependencies} builtinFields={ruleInputs?.builtinFields} />
    {issue ? <small className="field-rule-error">{issue}</small> : null}
  </div>
  return <div className="field-response-row">
    <div className="field-response-target" title={`${field.path} · ${field.type}`}><code>{field.path}</code><span>{field.type}</span></div><span className="field-response-arrow">←</span>
    <div className="field-value-control">
      {sourceMode ? <>
        <div className={`field-value-inputs${readOnly ? ' is-readonly' : ''}`}>
        {!readOnly ? <Select disabled={disabled} aria-label={`${field.path}值来源`} value={editing || processed ? 'business' : 'source'} options={[{ value: 'source', label: '数据源字段' }, ...VALUE_SOURCE_OPTIONS]} onChange={(kind) => {
          if (kind === 'builtin') return
          if (kind === 'business') { setEditing(true); return }
          if (kind === 'source') { direct(); return }
          setEditing(false)
          onChange(replaceFieldMapping(draft, { endpointField, mappingType: 'value_mapping', right: kind === 'fixed' ? { kind: 'fixed' } : { kind: 'endpoint' } }))
        }} /> : null}
        {editing || processed ? <BusinessRuleEntry title={field.path} configured={Boolean(ruleDescription)}
            inputCount={selected.length + requestDependencies.length + (ruleInputs?.builtinFields?.length || 0)} disabled={disabled} onClick={() => setEditing(true)} />
            : <Select allowClear showSearch optionFilterProp="label" disabled={disabled} placeholder="选择当前数据源字段" value={selected[0] ? sourceFieldKey(selected[0]) : undefined} options={options} onChange={direct} />}
        </div>
        {editing ? <RuleEditor title={`业务处理 · ${field.path}`} initial={{ dependencies: [...selected.map((source) => `source:${sourceFieldKey(source)}`), ...requestDependencies.map((item) => `endpoint:${apiDesignFieldKey(item)}`), ...(ruleInputs?.builtinFields || []).map((key) => `builtin:${key}`)], description: ruleDescription, missing: ruleInputs?.missingBehavior || 'error', defaultValue: ruleInputs?.defaultValue }} options={ruleOptions} type={field.type} disabled={disabled} canDirect={selected.length === 1} onDirect={() => direct(selected[0] && sourceFieldKey(selected[0]))} onApply={apply} onClose={() => setEditing(false)} /> : null}
      </> : <ValueRuleEditor title={field.path} fields={fields} type={field.type} disabled={disabled} readOnly={readOnly} onBusiness={() => setEditing(true)} onSource={() => direct()} right={valueMapping?.right} onChange={(right) => onChange(replaceFieldMapping(draft, right ? { endpointField, mappingType: 'value_mapping', right } : { endpointField, mappingType: 'unconfigured' }))} />}
    </div>
    {issue ? <small className="field-rule-error">{issue}</small> : null}
  </div>
}
