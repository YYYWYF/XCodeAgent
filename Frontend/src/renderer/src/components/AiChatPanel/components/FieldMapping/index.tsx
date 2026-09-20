import { confirmWorkspaceAction } from '../../../workspaceDialogs'
import { ApiOutlined, NodeIndexOutlined } from '@ant-design/icons'
import { Alert, Button, Empty, Radio, Select, Space, Spin, Tag } from 'antd'
import { useEffect, useState } from 'react'
import type { ReactElement } from 'react'
import type { DevelopmentPlanningApiContract, EndpointDesignSaveResult, WorkflowApiField } from '../../../../typings'
import type { BindingSelection } from '../../../../typings/endpointDesign'
import type { ApiDesignConfigTarget } from '../WorkflowRunCard/ApiDesignConfigModal'
import ApiDesignConfigModal from '../WorkflowRunCard/ApiDesignConfigModal'
import { apiDesignFieldKey, createUnconfiguredFieldMapping, validateApiDesignDraft } from '../WorkflowRunCard/apiDesignSerialization'
import ExternalMapping from './ExternalMapping'
import { mappingCandidates, selectionKey, setDirectMapping, sourceFieldKey, tableIsSelected } from './model'
import { useBindingWorkspace } from './useBindingWorkspace'
import type { BindingControl } from './useBindingWorkspace'
import './index.less'

type Props = {
  workspaceRoot: string; target?: ApiDesignConfigTarget; contracts: DevelopmentPlanningApiContract[]
  onSelect: (target: ApiDesignConfigTarget) => void; onOpenSources: () => void; onOpenExternalSources: () => void
  onSaved: (target: ApiDesignConfigTarget, result: EndpointDesignSaveResult) => void | Promise<void>
  onControl: (control?: BindingControl) => void
}

/** 在常驻页签内编辑直接映射，复杂映射仍交给原有编辑器。 */
export default function FieldMappingWorkspace({ workspaceRoot, target, contracts, onSelect, onOpenSources, onOpenExternalSources, onSaved, onControl }: Props): ReactElement {
  const state = useBindingWorkspace(workspaceRoot, target, onSaved)
  const { entry, catalog, tables, metadata, busy, loading, error, metadataLoading } = state
  const [kind, setKind] = useState<'database' | 'external_api'>()
  const [advanced, setAdvanced] = useState(false)
  const selection = entry?.value.selection
  const fields = entry?.preparation.payload.endpointFields || []
  const errors = entry ? validateApiDesignDraft(entry.value.draft) : {}
  const unavailableTable = Boolean(selection && !tableIsSelected(selection, tables))
  const missingFields = Boolean(entry && selection && metadata && fields.some((field) => {
    const mapping = entry.value.draft.fieldMappings.find((item) => apiDesignFieldKey(item.endpointField) === apiDesignFieldKey(field))
    return mapping?.mappingType === 'source_mapping' && !mappingCandidates(field, selection, metadata)
      .some((candidate) => sourceFieldKey(candidate) === sourceFieldKey(mapping.sourceFields[0]))
  }))
  const missingRequiredExternal = Boolean(entry && selection?.sourceType === 'external_api' && metadata && (metadata as { fields?: Array<{ section: string; path: string; required?: boolean }> }).fields?.some((field) => {
    if (!field.required || field.section === 'response_body') return false
    return !entry.value.draft.fieldMappings.some((mapping) => mapping.mappingType === 'source_mapping' && mapping.sourceFields.length === 1 && mapping.sourceFields[0].sourceType === 'external_api' && mapping.sourceFields[0].section === field.section && mapping.sourceFields[0].path === field.path)
  }))
  const editable = Boolean(entry && !entry.readOnly && !entry.complex && !entry.conflict)
  const withoutSource = fields.length === 0 && !selection && entry?.value.draft.fieldMappings.length === 0
  const canConfirm = editable && !Object.keys(errors).length && (withoutSource || fields.length > 0 && Boolean(selection && metadata) && !metadataLoading && !unavailableTable && !missingFields && !missingRequiredExternal)
  const step = !entry ? 0 : withoutSource ? 3 : !kind ? 1 : !selection ? 2 : 3

  useEffect(() => { setKind(undefined); setAdvanced(false) }, [state.key])
  useEffect(() => { if (selection) setKind(selection.sourceType) }, [state.key, selection?.sourceType])
  useEffect(() => {
    onControl(target && entry && !entry.readOnly && !entry.complex ? {
      key: state.key, target, step, busy, canConfirm, confirm: () => { void state.save(true) }
    } : undefined)
  }, [state.key, entry, busy, step, canConfirm, metadata, target, onControl])
  useEffect(() => () => onControl(undefined), [onControl])

  const candidates: Array<{ value: string; label: string; selection: BindingSelection }> = kind === 'database'
    ? tables.map((table) => ({ value: selectionKey({ ...table, sourceType: 'database' }),
      label: `${catalog.sources.find((source) => source.id === table.sourceId)?.name || table.sourceId} · ${table.table}${table.description ? `（${table.description}）` : ''}`,
      selection: { sourceType: 'database', sourceId: table.sourceId, schema: table.schema, table: table.table } }))
    : catalog.sources.flatMap((source) => source.type === 'external_api' ? source.directories.flatMap((directory) => directory.operations.map((operation) => {
      const next: BindingSelection = { sourceType: 'external_api', sourceId: source.id, directoryId: directory.id, operationId: operation.id }
      return { value: selectionKey(next), label: `${source.name} / ${directory.name} / ${operation.name} · ${operation.method} ${operation.path}`, selection: next }
    })) : [])
  const sourceLabel = candidates.find((item) => item.value === selectionKey(selection))?.label || (selection?.sourceType === 'database' ? selection.table : selection?.operationId) || '尚未选择'

  /** 更换来源必须显式放弃当前映射，取消不修改草稿。 */
  const changeSelection = (next: BindingSelection | null, nextKind = next?.sourceType): void => {
    if (!entry || selectionKey(next) === selectionKey(selection) && nextKind === kind) return
    /** 用户明确更换后同步重置来源与字段，防止跨对象残留。 */
    const apply = (): void => { setKind(nextKind); state.edit({ ...entry.value.draft, fieldMappings: fields.map(createUnconfiguredFieldMapping) }, next) }
    if (entry.value.draft.fieldMappings.some((item) => item.mappingType !== 'unconfigured')) {
      confirmWorkspaceAction({ title: '更换数据来源？', content: '当前字段映射将清空，已保存的正式映射在重新确认前不变。', okText: '更换并清空', cancelText: '取消', onOk: apply })
    } else apply()
  }

  /** 渲染对齐的直接映射行，缺失字段和类型错误在行内说明。 */
  const renderField = (field: WorkflowApiField): ReactElement => {
    const key = apiDesignFieldKey(field)
    const mapping = entry?.value.draft.fieldMappings.find((item) => apiDesignFieldKey(item.endpointField) === key)
    const mapped = mapping?.mappingType === 'source_mapping' ? mapping.sourceFields[0] : undefined
    const options = selection && metadata ? mappingCandidates(field, selection, metadata) : []
    const missing = Boolean(mapped && metadata && !options.some((item) => sourceFieldKey(item) === sourceFieldKey(mapped)))
    const issue = missing ? '来源字段已不存在，请重新选择。' : errors[key]
    const visibleOptions = mapped && !options.some((item) => sourceFieldKey(item) === sourceFieldKey(mapped)) ? [...options, mapped] : options
    return <div key={key} className={`binding-field${issue ? ' binding-field-pending' : ''}`}>
      <div className="binding-field-name" title={`${field.path} ${field.description || ''}`}><Tag>{field.side === 'request' ? '入参' : '出参'}</Tag><code>{field.path}</code>{field.description && <span>（{field.description}）</span>}<small>{field.type}{field.required ? ' · 必填' : ''}</small></div>
      <span aria-hidden="true">{field.side === 'request' ? '→' : '←'}</span>
      <div><Select allowClear showSearch optionFilterProp="label" disabled={!editable || busy || metadataLoading || !selection || unavailableTable}
        value={mapped ? sourceFieldKey(mapped) : undefined} placeholder="选择来源字段"
        options={visibleOptions.map((item) => ({ value: sourceFieldKey(item), label: `${item.sourceType === 'database' ? item.column : `${item.section} · ${item.path}`}${item.description ? `（${item.description}）` : ''} · ${item.type}`, disabled: missing && item === mapped }))}
        onChange={(value) => { if (entry) state.edit(setDirectMapping(entry.value.draft, field, options.find((item) => sourceFieldKey(item) === value))) }} />
      {mapped?.sourceType === 'database' && field.side === 'request' ? <Select className="binding-usage" disabled={!editable || busy} value={mapped.usage}
        options={[{ value: 'filter', label: '查询条件' }, { value: 'write', label: '写入字段' }]}
        onChange={(usage) => { if (entry) state.edit(setDirectMapping(entry.value.draft, field, { ...mapped, usage })) }} /> : null}
      {issue && !entry?.readOnly ? <small className="binding-field-error">{issue}</small> : null}</div>
    </div>
  }

  return <div className="binding-workspace">
    <nav className="binding-directory" aria-label="应用 API"><h4>应用 API</h4>{contracts.map((contract) => <section key={contract.id}>
      <small>{contract.label}</small>{contract.endpoints.map((endpoint) => <button key={endpoint.id} disabled={busy}
        className={target?.apiContractId === contract.id && target.endpointId === endpoint.id ? 'selected' : ''}
        onClick={() => onSelect({ apiContractId: contract.id, endpointId: endpoint.id, label: endpoint.summary || endpoint.path })}>
        <ApiOutlined />{endpoint.summary || endpoint.path}</button>)}</section>)}</nav>
    <main className="binding-editor">
      {!target ? <Empty description="请选择应用 API" /> : loading && !entry ? <Spin tip="正在读取 API 契约…" /> : null}
      {error ? <Alert type="error" showIcon message={error} action={<Space><Button onClick={state.refreshSources}>重试</Button>{entry && <Button disabled={busy} onClick={() => confirmWorkspaceAction({ title: '放弃草稿并重新加载？', content: '未确认的修改将被清除，正式映射不变。', okText: '放弃并加载', cancelText: '继续编辑', onOk: state.discard })}>重新加载</Button>}</Space>} /> : null}
      {entry && <>
        <header className="binding-heading"><div><strong><Tag>{String(entry.preparation.payload.endpoint.method || 'API')}</Tag><code>{String(entry.preparation.payload.endpoint.path || target?.endpointId)}</code></strong>
          <p>{target?.label || target?.endpointId} → {withoutSource ? '不绑定数据来源' : sourceLabel}</p></div><Space>
          {entry.readOnly ? <Button onClick={() => state.update({ ...entry, readOnly: false })}>修改映射</Button> : !entry.complex ? <>
            <Button loading={busy} disabled={entry.conflict} onClick={() => void state.save(false)}>保存</Button>
            <Button type="primary" loading={busy} disabled={!canConfirm} onClick={() => void state.save(true)}>保存并确认</Button>
          </> : null}</Space></header>
        {entry.conflict ? <Alert type="warning" message="草稿基于的契约或正式映射已变化，当前草稿已保留。" description="请核对当前内容后，明确放弃旧草稿并重新加载。"
          action={<Button disabled={busy} onClick={() => confirmWorkspaceAction({ title: '放弃旧草稿并重新加载？', okText: '放弃并加载', cancelText: '保留草稿', onOk: state.discard })}>重新加载</Button>} /> : null}
        {!fields.length && !entry.readOnly && <Alert type="info" message="当前接口没有可映射字段，可保存并确认不绑定数据来源。"
          description={selection ? '已有来源选择仅能保存为草稿。若无需来源绑定，请先明确清除选择，再保存并确认。' : '确认后仍需按原开发门禁检测并确认继续开发。'}
          action={selection ? <Button disabled={busy || entry.conflict} onClick={() => changeSelection(null)}>清除来源选择</Button> : undefined} />}
        {entry.complex ? <Alert type="info" message="当前接口包含复杂映射，继续使用原有编辑能力。" action={<Button onClick={() => setAdvanced(true)}>打开完整编辑器</Button>} /> : <>
          {!entry.readOnly && <section className="binding-section"><h4>数据来源</h4><Radio.Group disabled={busy || entry.conflict} value={kind} onChange={(event) => changeSelection(null, event.target.value)}>
            <Radio.Button value="database">数据表</Radio.Button><Radio.Button value="external_api">外部 API</Radio.Button></Radio.Group>
            {kind && <Select className="binding-target" showSearch optionFilterProp="label" placeholder={kind === 'database' ? '选择已添加的数据表' : '选择域名 / 目录 / 接口'}
              disabled={busy || entry.conflict} value={selection ? selectionKey(selection) : undefined} options={candidates}
              onChange={(value) => changeSelection(candidates.find((item) => item.value === value)?.selection || null)} />}
            <Space><Button onClick={(selection?.sourceType || kind) === 'external_api' ? onOpenExternalSources : onOpenSources}>打开{(selection?.sourceType || kind) === 'external_api' ? '外部 API' : '数据来源'}</Button><Button onClick={state.refreshSources}>重新检测</Button></Space>
            {kind && !candidates.length ? <p>暂无可绑定来源，请先配置{kind === 'database' ? '连接并添加数据表' : '外部 API 接口'}。</p> : null}
            {unavailableTable ? <Alert type="warning" message="所选表不在已添加清单中，请先添加后再确认。" /> : null}
          </section>}
          {selection && <Spin spinning={metadataLoading}>{selection.sourceType === 'external_api' && metadata && 'fields' in metadata ? <ExternalMapping busy={busy} draft={entry.value.draft} editable={editable} errors={errors} fields={fields} metadata={metadata} onChange={(draft) => state.edit(draft)} selection={selection} /> : <>
            <section className="binding-section"><h4>入参映射</h4>
              {(['path', 'query', 'header', 'request_body'] as const).map((location) => {
                const group = fields.filter((field) => field.side === 'request' && field.location === location)
                return group.length ? <div key={location}><h5>{ { path: 'Path', query: 'Query', header: 'Header', request_body: 'Body' }[location]}</h5>{group.map(renderField)}</div> : null
              })}{!fields.some((field) => field.side === 'request') && <p>无入参字段</p>}</section>
            <section className="binding-section"><h4>返回字段</h4>{fields.filter((field) => field.side === 'response').map(renderField)}
              {!fields.some((field) => field.side === 'response') && <p>无返回字段</p>}</section></>}</Spin>}
          {entry.readOnly && <p className="binding-readonly"><NodeIndexOutlined /> 映射已确认</p>}
        </>}
      </>}
    </main>
    <ApiDesignConfigModal open={advanced} target={target} workspaceRoot={workspaceRoot} onClose={() => setAdvanced(false)} onSaved={async (current, result) => { await onSaved(current, result); state.reload() }} />
  </div>
}
