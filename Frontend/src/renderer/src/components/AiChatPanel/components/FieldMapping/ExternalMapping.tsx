import { ArrowRightOutlined, SearchOutlined } from '@ant-design/icons'
import { Alert, Button, Checkbox, Input, Select, Space, Tag } from 'antd'
import { useMemo, useState } from 'react'
import type { ReactElement } from 'react'
import type { BindingSelection } from '../../../../typings/endpointDesign'
import type { WorkflowApiDesignDraft, WorkflowApiExternalFieldNode, WorkflowApiField, WorkflowApiSourceField } from '../../../../typings'
import type { ApiDesignExternalOperationMetadata } from '../../../../service/dataSources'
import { apiDesignFieldKey } from '../WorkflowRunCard/apiDesignSerialization'
import { setDirectMapping, sourceFieldKey } from './model'
import { cx } from '../../../../utils'

type Props = {
  fields: WorkflowApiField[]
  metadata: ApiDesignExternalOperationMetadata
  selection: BindingSelection
  draft: WorkflowApiDesignDraft
  editable: boolean
  busy: boolean
  errors: Record<string, string>
  onChange: (draft: WorkflowApiDesignDraft) => void
}

type ExternalField = NonNullable<ApiDesignExternalOperationMetadata['fields']>[number]

/** 返回外部字段的稳定身份，位置和路径共同避免同名字段冲突。 */
function externalKey(field: ExternalField): string {
  return `${field.section}:${field.path}`
}

/** 返回字段所在区域的中文名称。 */
function sectionLabel(section: string): string {
  return ({ path: 'Path', query: 'Query', header: 'Header', request_body: 'Body', response_body: 'Response' } as Record<string, string>)[section] || section
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

/** 统一读取外部响应字段和草稿来源的稳定键。 */
function responseFieldKey(field: ExternalField | WorkflowApiSourceField): string {
  return 'sourceType' in field ? sourceFieldKey(field) : externalKey(field)
}

/** 读取仍指向已删除外部字段的直接来源。 */
function directSource(mapping: WorkflowApiDesignDraft['fieldMappings'][number]): WorkflowApiSourceField | undefined {
  return mapping.mappingType === 'source_mapping' && mapping.sourceFields.length === 1 ? mapping.sourceFields[0] : undefined
}

/** 展示外部 API 的目标字段选择器，使用大箭头表达整体映射方向。 */
export default function ExternalMapping({ fields, metadata, selection, draft, editable, busy, errors, onChange }: Props): ReactElement {
  const [search, setSearch] = useState('')
  const [onlyUnconfigured, setOnlyUnconfigured] = useState(false)
  const externalFields = metadata.fields || []
  const requestTargets = externalFields.filter((field) => field.section !== 'response_body')
  const responseTargets = fields.filter((field) => field.side === 'response')
  const requestSources = fields.filter((field) => field.side === 'request')
  const responseSources = externalFields.filter((field) => field.section === 'response_body')
  const requestSections = ['path', 'query', 'header', 'request_body'] as const
  const normalizedSearch = search.trim().toLowerCase()

  /** 找出元数据已删除但草稿仍保留的外部请求来源，避免静默丢失用户选择。 */
  const staleRequestMappings = draft.fieldMappings.filter((mapping) => {
    if (mapping.mappingType !== 'source_mapping' || mapping.sourceFields.length !== 1) return false
    const source = mapping.sourceFields[0]
    return source.sourceType === 'external_api' && source.section !== 'response_body'
      && !requestTargets.some((target) => externalKey(target) === sourceFieldKey(source))
  })

  /** 根据区域和搜索条件过滤目标字段，筛选只影响展示不影响校验。 */
  const visibleTargets = (targets: ExternalField[] | WorkflowApiField[], isExternal: boolean): Array<ExternalField | WorkflowApiField> => targets.filter((target) => {
    const endpoint = isExternal ? mappedEndpoint(target as ExternalField, draft) : target as WorkflowApiField
    const key = endpoint ? apiDesignFieldKey(endpoint) : ''
    const mapping = endpoint ? draft.fieldMappings.find((item) => apiDesignFieldKey(item.endpointField) === key) : undefined
    const mapped = mapping?.mappingType === 'source_mapping' && mapping.sourceFields.length === 1
    if (onlyUnconfigured && mapped) return false
    if (!normalizedSearch) return true
    const text = isExternal
      ? `${(target as ExternalField).path} ${(target as ExternalField).description || ''} ${(target as ExternalField).section}`
      : `${(target as WorkflowApiField).path} ${(target as WorkflowApiField).description || ''}`
    return text.toLowerCase().includes(normalizedSearch)
  })

  /** 更新一个外部目标字段，保证同一应用字段不会残留旧的来源映射。 */
  const updateRequest = (target: ExternalField, value?: string): void => {
    if (!editable) return
    const current = mappedEndpoint(target, draft)
    let next = draft
    if (current) next = setDirectMapping(next, current)
    if (value) {
      const sourceEndpoint = requestSources.find((field) => apiDesignFieldKey(field) === value)
      if (sourceEndpoint) next = setDirectMapping(next, sourceEndpoint, sourceFor(target, selection))
    }
    onChange(next)
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

  /** 渲染整体方向提示，避免每一行重复放置箭头。 */
  const direction = (left: string, right: string): ReactElement => <div className="external-mapping-direction"><span>{left}</span><ArrowRightOutlined /><span>{right}</span></div>

  /** 渲染入参目标行：目标是外部字段，来源选择应用 API 入参。 */
  const renderRequest = (target: ExternalField): ReactElement => {
    const endpoint = mappedEndpoint(target, draft)
    const endpointKey = endpoint ? apiDesignFieldKey(endpoint) : ''
    const mapping = endpoint && draft.fieldMappings.find((item) => apiDesignFieldKey(item.endpointField) === endpointKey)
    const sourceValid = endpoint && mapping?.mappingType === 'source_mapping' && mapping.sourceFields.length === 1
    const issue = errors[endpointKey] || (target.required && !sourceValid ? '外部必填字段尚未映射。' : '')
    return <div className={cx('external-mapping-row', issue && 'binding-field-pending')} key={externalKey(target)}>
      <div className="external-mapping-target"><Tag>{sectionLabel(target.section)}</Tag><code>{target.path}</code>{target.description ? <span>（{target.description}）</span> : null}<small>{target.type}{target.required ? ' · 必填' : ''}</small></div>
      <Select allowClear showSearch optionFilterProp="label" disabled={!editable || busy} placeholder="选择应用 API 入参" value={endpointKey || undefined}
        options={requestSources.map((field) => { const occupied = draft.fieldMappings.find((item) => item.mappingType === 'source_mapping' && item.sourceFields.length === 1 && apiDesignFieldKey(item.endpointField) === apiDesignFieldKey(field) && apiDesignFieldKey(item.endpointField) !== endpointKey); return { value: apiDesignFieldKey(field), label: `${field.path}${field.description ? `（${field.description}）` : ''} · ${field.type}${occupied ? `（已被${occupied.endpointField.path}占用）` : ''}`, disabled: Boolean(occupied) } })}
        onChange={(value) => updateRequest(target, value)} />
      {issue && !busy ? <small className="binding-field-error">{issue}</small> : null}
    </div>
  }

  /** 渲染出参目标行：目标是应用字段，来源选择外部响应字段。 */
  const renderResponse = (target: WorkflowApiField): ReactElement => {
    const mapping = draft.fieldMappings.find((item) => apiDesignFieldKey(item.endpointField) === apiDesignFieldKey(target))
    const mapped = mapping?.mappingType === 'source_mapping' && mapping.sourceFields.length === 1 ? mapping.sourceFields[0] : undefined
    const sourceValid = mapped?.sourceType === 'external_api' && responseSources.some((field) => externalKey(field) === responseFieldKey(mapped))
    const issue = errors[apiDesignFieldKey(target)] || (mapped && !sourceValid ? '来源字段已不存在，请重新选择。' : '')
    const responseOptions: Array<ExternalField | WorkflowApiSourceField> = mapped && !sourceValid ? [mapped, ...responseSources] : responseSources
    return <div className={cx('external-mapping-row', issue && 'binding-field-pending')} key={apiDesignFieldKey(target)}>
      <div className="external-mapping-target"><code>{target.path}</code>{target.description ? <span>（{target.description}）</span> : null}<small>{target.type}{target.required ? ' · 必填' : ''}</small></div>
      <Select allowClear showSearch optionFilterProp="label" disabled={!editable || busy} placeholder="选择外部 API 出参" value={mapped ? sourceFieldKey(mapped) : undefined}
        options={responseOptions.map((field) => { const fieldKey = responseFieldKey(field); const stale = !responseSources.some((item) => externalKey(item) === fieldKey); const occupied = draft.fieldMappings.find((item) => item.mappingType === 'source_mapping' && item.sourceFields.some((source) => sourceFieldKey(source) === fieldKey) && apiDesignFieldKey(item.endpointField) !== apiDesignFieldKey(target)); const labelPath = 'column' in field ? field.column : field.path; return { value: fieldKey, label: `${labelPath}${field.description ? `（${field.description}）` : ''} · ${field.type}${stale ? '（字段已失效）' : occupied ? `（已被${occupied.endpointField.path}占用）` : ''}`, disabled: stale || Boolean(occupied) } })}
        onChange={(value) => updateResponse(target, value)} />
      {issue && !busy ? <small className="binding-field-error">{issue}</small> : null}
    </div>
  }

  const visibleRequest = useMemo(() => visibleTargets(requestTargets, true) as ExternalField[], [requestTargets, draft, normalizedSearch, onlyUnconfigured])
  const visibleResponse = useMemo(() => visibleTargets(responseTargets, false) as WorkflowApiField[], [responseTargets, draft, normalizedSearch, onlyUnconfigured])
  return <div className="external-mapping">
    <div className="external-mapping-toolbar"><Input allowClear prefix={<SearchOutlined />} placeholder="搜索字段名称或说明" value={search} onChange={(event) => setSearch(event.target.value)} /><Checkbox checked={onlyUnconfigured} onChange={(event) => setOnlyUnconfigured(event.target.checked)}>仅看未配置</Checkbox></div>
    <section className="binding-section"><div className="external-mapping-title">入参映射</div>{direction('应用 API 入参', '外部 API 入参')}
      {staleRequestMappings.length ? <Alert type="warning" showIcon message="部分外部入参已失效，请清除后重新选择。" description={<Space direction="vertical" size={4}>{staleRequestMappings.map((mapping) => { const source = directSource(mapping); return <span key={apiDesignFieldKey(mapping.endpointField)}><code>{mapping.endpointField.path}</code> ← <code>{source ? sourceFieldKey(source) : '未知字段'}</code><Button type="link" size="small" onClick={() => onChange(setDirectMapping(draft, mapping.endpointField as WorkflowApiField))}>清除</Button></span> })}</Space>} /> : null}
      {requestSections.map((section) => {
        const group = visibleRequest.filter((target) => target.section === section)
        return group.length ? <div className="external-mapping-group" key={section}><h5>{sectionLabel(section)}</h5>{group.map(renderRequest)}</div> : null
      })}
      {!visibleRequest.length ? <Alert type="info" message="没有符合条件的外部入参。" /> : null}
      {requestSources.some((field) => { const mapping = draft.fieldMappings.find((item) => apiDesignFieldKey(item.endpointField) === apiDesignFieldKey(field)); const configured = mapping?.mappingType === 'source_mapping' && mapping.sourceFields.length === 1; return !configured }) ? <Alert type="warning" showIcon message="应用 API 入参尚未全部映射。" description={requestSources.filter((field) => { const mapping = draft.fieldMappings.find((item) => apiDesignFieldKey(item.endpointField) === apiDesignFieldKey(field)); const configured = mapping?.mappingType === 'source_mapping' && mapping.sourceFields.length === 1; return !configured }).map((field) => `${field.path}${field.description ? `（${field.description}）` : ''}`).join('、')} /> : null}
    </section>
    <section className="binding-section"><div className="external-mapping-title">出参映射</div><div className="external-mapping-direction"><span>外部 API 出参</span><ArrowRightOutlined /><span>应用 API 出参</span></div>{visibleResponse.map(renderResponse)}{!visibleResponse.length ? <Alert type="info" message="没有符合条件的应用出参。" /> : null}</section>
  </div>
}
