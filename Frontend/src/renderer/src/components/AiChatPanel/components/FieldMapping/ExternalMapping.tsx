import { DeleteOutlined } from '@ant-design/icons'
import { Alert, Button, Input, Select, Space } from 'antd'
import { useState, type ReactElement } from 'react'
import type { BindingSelection } from '../../../../typings/endpointDesign'
import type { WorkflowApiDesignDraft, WorkflowApiExternalApiFixedValueDraft, WorkflowApiExternalFieldNode, WorkflowApiField, WorkflowApiSourceField } from '../../../../typings'
import type { ApiDesignExternalOperationMetadata } from '../../../../service/dataSources'
import { apiDesignFieldKey, typeFamily } from '../WorkflowRunCard/apiDesignSerialization'
import { setDirectMapping, sourceFieldKey } from './model'

type Props = {
  fields: WorkflowApiField[]
  metadata: ApiDesignExternalOperationMetadata
  selection: BindingSelection
  draft: WorkflowApiDesignDraft
  editable: boolean
  readOnly: boolean
  busy: boolean
  errors: Record<string, string>
  onChange: (draft: WorkflowApiDesignDraft) => void
}

type ExternalField = NonNullable<ApiDesignExternalOperationMetadata['fields']>[number]

const EXTERNAL_VALUE_SOURCE_OPTIONS = [
  { value: 'endpoint', label: '接口参数' },
  { value: 'fixed', label: '固定值' },
  { value: 'builtin', label: '内置参数', disabled: true }
]

/** 返回外部字段的稳定身份，位置和路径共同避免同名字段冲突。 */
function externalKey(field: { section: string; path: string }): string {
  return `${field.section}:${field.path}`
}

/** 返回字段所在区域的中文名称。 */
function sectionLabel(section: string): string {
  return ({ path: 'Path', query: 'Query', header: 'Header', request_body: 'Body', response_body: 'Response' } as Record<string, string>)[section] || section
}

type FieldSummaryProps = {
  path: string
  description?: string
  type: string
  required?: boolean
  requiredUnmapped?: boolean
  sideLabel: string
}

/** 统一按字段名、说明和类型单行展示，并为窄屏提供字段方向标签。 */
function FieldSummary({ path, description, type, required, requiredUnmapped, sideLabel }: FieldSummaryProps): ReactElement {
  const fieldLabel = `${path}${description ? `（${description}）` : ''} · ${type}`
  return <div className="external-mapping-target">
    <span className="external-mapping-target-side">{sideLabel}</span>
    <div className="external-mapping-target-main">
      <span className="external-mapping-target-identity">
        <span className="external-mapping-target-name" title={fieldLabel}>
          {path}{description ? <span className="external-mapping-target-description">（{description}）</span> : null}<span className="external-mapping-target-type"> · {type}</span>
        </span>
        {required ? <span className={`external-mapping-required${requiredUnmapped ? ' is-unmapped' : ''}`}>{requiredUnmapped ? '必填 · 未映射' : '必填'}</span> : null}
      </span>
    </div>
  </div>
}

/** 将当前目标字段恢复为对应的应用契约字段。 */
function mappedEndpoint(target: ExternalField, draft: WorkflowApiDesignDraft): WorkflowApiField | undefined {
  return draft.fieldMappings.find((mapping) => {
    const source = mapping.mappingType === 'source_mapping' && mapping.sourceFields.length === 1 ? mapping.sourceFields[0] : undefined
    return source?.sourceType === 'external_api' && sourceFieldKey(source) === externalKey(target)
  })?.endpointField as WorkflowApiField | undefined
}

/** 创建当前外部目标字段对应的来源字段，仍使用现有正式映射结构。 */
function sourceFor(target: ExternalField, selection: BindingSelection): Omit<WorkflowApiExternalFieldNode, 'id' | 'nodeType'> {
  if (selection.sourceType !== 'external_api') throw new Error('外部映射必须使用外部 API 来源。')
  return { sourceType: 'external_api', sourceId: selection.sourceId, directoryId: selection.directoryId, operationId: selection.operationId, section: target.section as WorkflowApiExternalFieldNode['section'], path: target.path, type: target.type, description: target.description }
}

/** 将固定值草稿转换为控件文本。 */
function fixedValueText(value: unknown): string {
  return String(value ?? '')
}

/** 按外部参数类型将输入文本转为可保存的标量。 */
function parseFixedValue(raw: string, fieldType: string): unknown {
  if (!raw.trim()) return undefined
  const family = typeFamily(fieldType)
  if (family === 'number') {
    const value = Number(raw)
    return Number.isFinite(value) ? value : raw
  }
  if (family === 'boolean') return raw.trim() === 'true'
  return raw
}

/** 统一读取外部响应字段和草稿来源的稳定键。 */
function responseFieldKey(field: ExternalField | WorkflowApiSourceField): string {
  return 'sourceType' in field ? sourceFieldKey(field) : externalKey(field)
}

/** 读取仍指向已删除外部字段的直接来源。 */
function directSource(mapping: WorkflowApiDesignDraft['fieldMappings'][number]): WorkflowApiSourceField | undefined {
  return mapping.mappingType === 'source_mapping' && mapping.sourceFields.length === 1 ? mapping.sourceFields[0] : undefined
}

/** 以目标字段、值来源和参数三列展示外部 API 字段映射。 */
export default function ExternalMapping({ fields, metadata, selection, draft, editable, readOnly, busy, errors, onChange }: Props): ReactElement {
  const externalFields = metadata.fields || []
  const allRequestTargets = externalFields.filter((field) => field.section !== 'response_body')
  const responseTargets = fields.filter((field) => field.side === 'response')
  const requestSources = fields.filter((field) => field.side === 'request')
  const responseSources = externalFields.filter((field) => field.section === 'response_body')
  const requestSections = ['path', 'query', 'header', 'request_body'] as const
  const fixedValues = draft.externalApiFixedValues || []
  const currentOperationKey = selection.sourceType === 'external_api'
    ? JSON.stringify([selection.sourceId, selection.directoryId, selection.operationId])
    : ''
  const [hiddenOptionalFields, setHiddenOptionalFields] = useState<{ operationKey: string; keys: string[] }>({ operationKey: '', keys: [] })
  const hiddenFieldKeys = new Set(hiddenOptionalFields.operationKey === currentOperationKey ? hiddenOptionalFields.keys : [])
  const requestTargets = allRequestTargets.filter((field) => !hiddenFieldKeys.has(externalKey(field)))
  /** 找出元数据已删除但草稿仍保留的外部请求来源，避免静默丢失用户选择。 */
  const staleRequestMappings = draft.fieldMappings.filter((mapping) => {
    if (mapping.mappingType !== 'source_mapping' || mapping.sourceFields.length !== 1) return false
    const source = mapping.sourceFields[0]
    return source.sourceType === 'external_api' && source.section !== 'response_body'
      && (selection.sourceType !== 'external_api' || source.sourceId !== selection.sourceId
        || source.directoryId !== selection.directoryId || source.operationId !== selection.operationId
        || !requestTargets.some((target) => externalKey(target) === sourceFieldKey(source)))
  })
  /** 找出不属于当前 Operation 或已从元数据移除的固定值目标，避免隐藏后无法清理。 */
  const staleRequestFixedValues = fixedValues.filter((item) => {
    const source = item.externalField
    return selection.sourceType !== 'external_api' || source.sourceId !== selection.sourceId
      || source.directoryId !== selection.directoryId || source.operationId !== selection.operationId
      || !requestTargets.some((target) => externalKey(target) === externalKey(source))
  })

  /** 更新一个外部目标字段，保证同一应用字段不会残留旧的来源映射。 */
  const updateRequest = (target: ExternalField, value?: string): void => {
    if (!editable) return
    const current = mappedEndpoint(target, draft)
    let next = draft
    if (current) next = setDirectMapping(next, current)
    next = { ...next, externalApiFixedValues: fixedValues.filter((item) => externalKey(item.externalField) !== externalKey(target)) }
    if (value) {
      const sourceEndpoint = requestSources.find((field) => apiDesignFieldKey(field) === value)
      if (sourceEndpoint) next = setDirectMapping(next, sourceEndpoint, sourceFor(target, selection))
    }
    onChange(next)
  }

  /** 切换外部参数的接口参数或固定值来源，并清除另一种来源。 */
  const changeRequestValueSource = (target: ExternalField, kind: 'endpoint' | 'fixed'): void => {
    if (!editable) return
    const current = mappedEndpoint(target, draft)
    let next = current ? setDirectMapping(draft, current) : draft
    const existingFixed = fixedValues.find((item) => externalKey(item.externalField) === externalKey(target))
    next = { ...next, externalApiFixedValues: fixedValues.filter((item) => externalKey(item.externalField) !== externalKey(target)) }
    if (kind === 'fixed') {
      const value = existingFixed?.value
      next = { ...next, externalApiFixedValues: [...(next.externalApiFixedValues || []), { externalField: sourceFor(target, selection), ...(value !== undefined ? { value } : {}) }] }
    }
    onChange(next)
  }

  /** 更新一条外部请求参数的固定值。 */
  const updateRequestFixedValue = (target: ExternalField, value: unknown): void => {
    if (!editable) return
    const existing = fixedValues.find((item) => externalKey(item.externalField) === externalKey(target))
    const entry: WorkflowApiExternalApiFixedValueDraft = {
      externalField: sourceFor(target, selection),
      ...(value !== undefined ? { value } : {})
    }
    const nextValues = existing
      ? fixedValues.map((item) => externalKey(item.externalField) === externalKey(target) ? entry : item)
      : [...fixedValues, entry]
    onChange({ ...draft, externalApiFixedValues: nextValues })
  }

  /** 删除当前编辑界面中的非必填外部请求字段，不把删除状态保存到草稿或正式产物。 */
  const deleteOptionalRequestField = (target: ExternalField): void => {
    if (!editable || target.required) return
    const current = mappedEndpoint(target, draft)
    const next = current ? setDirectMapping(draft, current) : draft
    setHiddenOptionalFields((current) => ({
      operationKey: currentOperationKey,
      keys: [...new Set([...(current.operationKey === currentOperationKey ? current.keys : []), externalKey(target)])]
    }))
    onChange({ ...next, externalApiFixedValues: fixedValues.filter((item) => externalKey(item.externalField) !== externalKey(target)) })
  }

  /** 更新一个应用出参目标字段，来源为外部响应字段。 */
  const updateResponse = (target: WorkflowApiField, value?: string): void => {
    if (!editable) return
    const current = draft.fieldMappings.find((mapping) => apiDesignFieldKey(mapping.endpointField) === apiDesignFieldKey(target))
    let next = current ? setDirectMapping(draft, target) : draft
    if (value) {
      const source = responseSources.find((item) => externalKey(item) === value)
      if (source) next = setDirectMapping(next, target, sourceFor(source, selection))
    }
    onChange(next)
  }

  /** 渲染入参映射行：展示外部 API 目标字段，并选择应用 API 来源字段。 */
  const renderRequest = (target: ExternalField): ReactElement => {
    const endpoint = mappedEndpoint(target, draft)
    const endpointKey = endpoint ? apiDesignFieldKey(endpoint) : ''
    const mapping = endpoint && draft.fieldMappings.find((item) => apiDesignFieldKey(item.endpointField) === endpointKey)
    const fixed = fixedValues.find((item) => externalKey(item.externalField) === externalKey(target))
    const sourceKind = fixed ? 'fixed' : 'endpoint'
    const sourceValid = Boolean(endpoint && mapping?.mappingType === 'source_mapping' && mapping.sourceFields.length === 1)
      || Boolean(fixed && fixed.value !== undefined && fixed.value !== null && fixed.value !== '')
    const issue = errors[endpointKey] || ''
    return <div className={`external-mapping-row is-request${issue ? ' binding-field-pending' : ''}`} key={externalKey(target)}>
      <div className="external-mapping-field-cell"><FieldSummary path={target.path} description={target.description} type={target.type} required={target.required} requiredUnmapped={Boolean(target.required && !sourceValid)} sideLabel="外部 API 入参" /></div>
      <div className="external-mapping-source-cell">
        {readOnly ? <span className="external-mapping-source-label">{sourceKind === 'fixed' ? '固定值' : '接口参数'}</span>
          : <Select className="external-mapping-source-select" disabled={!editable || busy} value={sourceKind} options={EXTERNAL_VALUE_SOURCE_OPTIONS}
            onChange={(kind: 'endpoint' | 'fixed') => changeRequestValueSource(target, kind)} />}
      </div>
      <div className="external-mapping-parameter-cell">
        {readOnly ? fixed
          ? <span className="external-mapping-fixed-summary">{fixedValueText(fixed.value) || '—'}</span>
          : <span className="external-mapping-fixed-summary">{endpoint ? `${endpoint.path} · ${endpoint.type}` : '—'}</span>
          : sourceKind === 'endpoint' ? <Select className="external-mapping-parameter-select" allowClear showSearch optionFilterProp="label" disabled={!editable || busy} placeholder="选择应用 API 入参" value={endpointKey || undefined}
            options={requestSources.map((field) => { const occupied = draft.fieldMappings.find((item) => item.mappingType === 'source_mapping' && item.sourceFields.length === 1 && apiDesignFieldKey(item.endpointField) === apiDesignFieldKey(field) && apiDesignFieldKey(item.endpointField) !== endpointKey); return { value: apiDesignFieldKey(field), label: `${field.path}${field.description ? `（${field.description}）` : ''} · ${field.type}${field.required ? ' · 必填' : ''}${occupied ? `（已被${occupied.endpointField.path}占用）` : ''}`, disabled: Boolean(occupied) } })}
            onChange={(value) => updateRequest(target, value)} />
          : typeFamily(target.type) === 'boolean' ? <Select className="external-mapping-parameter-select" placeholder="选择固定值" disabled={!editable || busy} value={typeof fixed?.value === 'boolean' ? String(fixed.value) : undefined} options={[{ value: 'true', label: 'true' }, { value: 'false', label: 'false' }]}
            onChange={(value) => updateRequestFixedValue(target, value === 'true')} />
            : <Input className="external-mapping-parameter-input" disabled={!editable || busy} type={typeFamily(target.type) === 'number' ? 'number' : 'text'} placeholder="输入固定值" value={fixedValueText(fixed?.value)}
              onChange={(event) => updateRequestFixedValue(target, parseFixedValue(event.target.value, target.type))} />}
      </div>
      {!readOnly ? <Button className="external-mapping-delete" danger type="text" disabled={!editable || busy || Boolean(target.required)}
        title={target.required ? '必填字段不能删除' : '删除此可选字段'}
        icon={<DeleteOutlined />} aria-label={`删除外部 API 可选参数 ${target.path}`} onClick={() => deleteOptionalRequestField(target)} /> : <span aria-hidden="true" />}
      {issue && !busy ? <small className="binding-field-error">{issue}</small> : null}
    </div>
  }

  /** 渲染出参映射行：展示应用 API 目标字段，并选择外部 API 来源字段。 */
  const renderResponse = (target: WorkflowApiField): ReactElement => {
    const mapping = draft.fieldMappings.find((item) => apiDesignFieldKey(item.endpointField) === apiDesignFieldKey(target))
    const mapped = mapping?.mappingType === 'source_mapping' && mapping.sourceFields.length === 1 ? mapping.sourceFields[0] : undefined
    const sourceValid = mapped?.sourceType === 'external_api' && responseSources.some((field) => externalKey(field) === responseFieldKey(mapped))
    const issue = errors[apiDesignFieldKey(target)] || (mapped && !sourceValid ? '来源字段已不存在，请重新选择。' : '')
    const responseOptions: Array<ExternalField | WorkflowApiSourceField> = mapped && !sourceValid ? [mapped, ...responseSources] : responseSources
    return <div className={`external-mapping-row is-response${issue ? ' binding-field-pending' : ''}`} key={apiDesignFieldKey(target)}>
      <FieldSummary path={target.path} description={target.description} type={target.type} sideLabel="应用 API 出参" />
      {readOnly ? mapped?.sourceType === 'external_api'
        ? <FieldSummary path={mapped.path} description={mapped.description} type={mapped.type} sideLabel="外部 API 出参" />
        : <span className="external-mapping-unmapped">—</span>
        : <Select allowClear showSearch optionFilterProp="label" disabled={!editable || busy} placeholder="选择外部 API 出参" value={mapped ? sourceFieldKey(mapped) : undefined}
        options={responseOptions.map((field) => { const fieldKey = responseFieldKey(field); const stale = !responseSources.some((item) => externalKey(item) === fieldKey); const labelPath = 'column' in field ? field.column : field.path; return { value: fieldKey, label: `${labelPath}${field.description ? `（${field.description}）` : ''} · ${field.type}${stale ? '（字段已失效）' : ''}`, disabled: stale } })}
        onChange={(value) => updateResponse(target, value)} />}
      {issue && !busy ? <small className="binding-field-error">{issue}</small> : null}
    </div>
  }

  return <div className="external-mapping">
    <section className="binding-section external-mapping-section">
      <div className="external-mapping-section-heading"><div className="external-mapping-title">入参映射</div></div>
      {staleRequestMappings.length || staleRequestFixedValues.length ? <Alert className="external-mapping-stale-alert" type="warning" showIcon message="部分外部入参配置已失效，请清除后重新确认。" description={<Space direction="vertical" size={4}>
        {staleRequestMappings.map((mapping) => { const source = directSource(mapping); return <span key={apiDesignFieldKey(mapping.endpointField)}><code>{mapping.endpointField.path}</code> ← <code>{source ? sourceFieldKey(source) : '未知字段'}</code><Button type="link" size="small" disabled={!editable || busy} onClick={() => onChange(setDirectMapping(draft, mapping.endpointField as WorkflowApiField))}>清除</Button></span> })}
        {staleRequestFixedValues.map((item) => <span key={`fixed:${JSON.stringify([item.externalField.sourceId, item.externalField.directoryId, item.externalField.operationId, item.externalField.section, item.externalField.path])}`}><code>{sectionLabel(item.externalField.section)}.{item.externalField.path}</code> ← <code>固定值：{fixedValueText(item.value) || '未填写'}</code><Button type="link" size="small" disabled={!editable || busy} onClick={() => onChange({ ...draft, externalApiFixedValues: fixedValues.filter((candidate) => candidate !== item) })}>清除</Button></span>)}
      </Space>} /> : null}
      {requestTargets.length ? <div className="external-mapping-table is-grouped">
        <div className="external-mapping-table-head"><span>外部 API 参数</span><span>值来源</span><span>参数</span><span aria-hidden="true" /></div>
        {requestSections.map((section) => {
          const group = requestTargets.filter((target) => target.section === section)
          return group.length ? <div className="external-mapping-group" key={section}><h5>{sectionLabel(section)}</h5>{group.map(renderRequest)}</div> : null
        })}
      </div> : <div className="external-mapping-empty">{allRequestTargets.length ? '当前编辑中已移除全部可选入参。' : '当前外部 API 没有可映射的入参。'}</div>}
      {errors.__externalApiFixedValues && !busy ? <small className="binding-field-error">{errors.__externalApiFixedValues}</small> : null}
      {requestSources.some((field) => { const mapping = draft.fieldMappings.find((item) => apiDesignFieldKey(item.endpointField) === apiDesignFieldKey(field)); const configured = mapping?.mappingType === 'source_mapping' && mapping.sourceFields.length === 1; return !configured }) ? <Alert type="warning" showIcon message="应用 API 入参尚未全部映射。" description={requestSources.filter((field) => { const mapping = draft.fieldMappings.find((item) => apiDesignFieldKey(item.endpointField) === apiDesignFieldKey(field)); const configured = mapping?.mappingType === 'source_mapping' && mapping.sourceFields.length === 1; return !configured }).map((field) => `${field.path}${field.description ? `（${field.description}）` : ''}`).join('、')} /> : null}
    </section>
    <section className="binding-section external-mapping-section">
      <div className="external-mapping-section-heading"><div className="external-mapping-title">出参映射</div></div>
      {responseTargets.length ? <div className="external-mapping-table">
        <div className="external-mapping-table-head"><span>应用 API 出参</span><span>外部 API 出参</span></div>
        {responseTargets.map(renderResponse)}
      </div> : <div className="external-mapping-empty">当前应用 API 没有可映射的出参。</div>}
    </section>
  </div>
}
