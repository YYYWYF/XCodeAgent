import { confirmWorkspaceAction } from '../../../workspaceDialogs'
import { ApiOutlined, CaretDownOutlined, DownOutlined, EditOutlined, FolderOpenOutlined, NodeIndexOutlined, UpOutlined } from '@ant-design/icons'
import { Alert, Button, Empty, Radio, Select, Space, Spin, Tag, Tooltip } from 'antd'
import { useCallback, useEffect, useState } from 'react'
import type { ReactElement } from 'react'
import type { DevelopmentPlanningApiContract, EndpointDesignSaveResult, WorkflowApiDesignDraft } from '../../../../typings'
import type { BindingSelection } from '../../../../typings/endpointDesign'
import { cx } from '../../../../utils'
import type { ApiDesignConfigTarget } from '../WorkflowRunCard/ApiDesignConfigModal'
import ApiDesignConfigModal from '../WorkflowRunCard/ApiDesignConfigModal'
import { apiDesignFieldKey, createUnconfiguredFieldMapping, defaultDatabaseOperation, validateApiDesignDraft } from '../WorkflowRunCard/apiDesignSerialization'
import { apiEndpointDisplayPath } from '../../utils'
import ExternalMapping from './ExternalMapping'
import DatabaseMapping from './DatabaseMapping'
import StaticMapping, { StaticDataCard } from './StaticMapping'
import { mappingCandidates, resetDraftForDatabaseOperation, selectionKey, tableIsSelected } from './model'
import { useBindingWorkspace } from './useBindingWorkspace'
import { RuleEditingContext } from './RuleEditor'
import './index.less'

type Props = {
  workspaceRoot: string; target?: ApiDesignConfigTarget; contracts: DevelopmentPlanningApiContract[]
  onSelect: (target: ApiDesignConfigTarget) => void
  onSaved: (target: ApiDesignConfigTarget, result: EndpointDesignSaveResult) => void | Promise<void>
}

/** 在常驻页签内编辑直接映射，复杂映射仍交给原有编辑器。 */
export default function FieldMappingWorkspace({ workspaceRoot, target, contracts, onSelect, onSaved }: Props): ReactElement {
  const state = useBindingWorkspace(workspaceRoot, target, onSaved)
  const { entry, catalog, tables, metadata, busy, loading, error, metadataLoading } = state
  const [editingRules, setEditingRules] = useState(0)
  /** 汇总尚未应用的规则编辑器，防止确认旧值。 */
  const trackRuleEditing = useCallback((delta: number) => setEditingRules((count) => count + delta), [])
  const [kind, setKind] = useState<BindingSelection['sourceType']>()
  const [sourceChanging, setSourceChanging] = useState(false)
  const [pendingSelection, setPendingSelection] = useState<BindingSelection | null>(null)
  const [advanced, setAdvanced] = useState(false)
  const [sourceCollapsed, setSourceCollapsed] = useState(false)
  const [validation, setValidation] = useState<{ draft: WorkflowApiDesignDraft; errors: Record<string, string> }>()
  const selection = entry?.value.selection
  const fields = entry?.preparation.payload.endpointFields || []
  const validationErrors = entry ? validateApiDesignDraft(entry.value.draft) : {}
  // 校验仅属于本次提交的草稿；清空、切换或继续编辑后不沿用旧提示。
  const errors = !entry?.readOnly && validation?.draft === entry?.value.draft ? validation?.errors || {} : {}
  const unavailableTable = Boolean(selection && !tableIsSelected(selection, tables))
  const missingFields = Boolean(entry && selection && metadata && fields.some((field) => {
    const mapping = entry.value.draft.fieldMappings.find((item) => apiDesignFieldKey(item.endpointField) === apiDesignFieldKey(field))
    if (mapping?.mappingType !== 'source_mapping') return false
    return mapping.sourceFields.some((source) => source.sourceType === 'database' && !mappingCandidates(field, selection, metadata).some((candidate) =>
      candidate.sourceType === 'database' && candidate.sourceId === source.sourceId && candidate.schema === source.schema
      && candidate.table === source.table && candidate.column === source.column && candidate.type === source.type))
  }))
  const missingQueryColumns = Boolean(selection?.sourceType === 'database' && metadata && 'columns' in metadata && entry?.value.draft.databaseQuery?.items.some((item) =>
    (item.kind === 'group' ? item.items : [item]).some((condition) => {
      // 空列表示刚添加的未完成条件行，不应当作失效的历史列提示。
      if (!condition.column) return false
      return condition.sourceId !== selection.sourceId || condition.schema !== selection.schema || condition.table !== selection.table
        || !(metadata.columns || []).some((column) => column.name === condition.column && column.type === condition.type)
    })))
  const missingRequiredExternal = Boolean(entry && selection?.sourceType === 'external_api' && metadata && (metadata as { fields?: Array<{ section: string; path: string; required?: boolean }> }).fields?.some((field) => {
    if (!field.required || field.section === 'response_body') return false
    return !entry.value.draft.externalApiBindings?.some((item) => item.externalField.section === field.section && item.externalField.path === field.path && item.right)
  }))
  const editable = Boolean(entry && !entry.readOnly && !entry.complex && !entry.conflict)
    const withoutSource = fields.length === 0 && !selection && entry?.value.draft.fieldMappings.length === 0 && !entry?.value.draft.externalApiBindings?.length
  const canConfirm = editable && editingRules === 0
    && (withoutSource || fields.length > 0 && Boolean(selection && (selection.sourceType === 'static' || metadata)) && !metadataLoading && !unavailableTable && !missingFields && !missingQueryColumns && !missingRequiredExternal)
  const databaseSelection = selection?.sourceType === 'database' ? selection : undefined

  useEffect(() => { setKind(undefined); setSourceChanging(false); setPendingSelection(null); setAdvanced(false); setSourceCollapsed(false); setValidation(undefined) }, [state.key, entry?.readOnly])
  useEffect(() => { if (selection) setKind(selection.sourceType) }, [state.key, selection?.sourceType])

  const candidates: Array<{ value: string; label: string; selection: BindingSelection }> = kind === 'database'
    ? tables.map((table) => ({ value: selectionKey({ ...table, sourceType: 'database' }), label: `${catalog.sources.find((source) => source.id === table.sourceId)?.name || table.sourceId} · ${table.table}${table.description ? `（${table.description}）` : ''}`, selection: { sourceType: 'database', sourceId: table.sourceId, schema: table.schema, table: table.table } }))
    : catalog.sources.flatMap((source) => source.type === 'external_api' ? source.directories.flatMap((directory) => directory.operations.map((operation) => {
      const next: BindingSelection = { sourceType: 'external_api', sourceId: source.id, directoryId: directory.id, operationId: operation.id }
      return { value: selectionKey(next), label: `${source.name} / ${directory.name} / ${operation.name} · ${operation.method} ${operation.path}`, selection: next }
    })) : [])
  const sourceLabel = selection?.sourceType === 'static' ? '自定义静态数据' : candidates.find((item) => item.value === selectionKey(selection))?.label || (selection?.sourceType === 'database' ? selection.table : selection?.operationId) || '尚未选择'
  const activeContract = target ? contracts.find((contract) => contract.id === target.apiContractId || contract.endpoints.some((endpoint) => endpoint.id === target.endpointId && (endpoint.apiContractId || contract.id) === target.apiContractId)) : undefined
  const activeEndpoint = activeContract?.endpoints.find((endpoint) => endpoint.id === target?.endpointId)
  const activeContractName = activeContract?.name || '未命名接口分组'
  const activeEndpointName = activeEndpoint?.name || String(entry?.preparation.payload.endpoint.name || '') || target?.label || target?.endpointId || '未命名接口'
  const activeEndpointPath = String(entry?.preparation.payload.endpoint.path || activeEndpoint?.path || target?.endpointId || '/')

  /** 更换来源必须显式放弃当前映射，取消不修改草稿。 */
  const changeSelection = (next: BindingSelection | null, nextKind = next?.sourceType): void => {
    if (!entry || selectionKey(next) === selectionKey(selection) && nextKind === kind) return
    const apply = (): void => {
      setKind(nextKind)
      setSourceChanging(false)
      setPendingSelection(null)
      setValidation(undefined)
      state.edit({ ...entry.value.draft, sourceBinding: next || undefined, staticData: undefined, implementationDescription: next?.sourceType === 'static' ? '' : entry.value.draft.implementationDescription, databaseOperation: next?.sourceType === 'database' ? defaultDatabaseOperation(String(entry.preparation.payload.endpoint.method || '')) : undefined, databaseQuery: undefined, databaseWrites: [], externalApiBindings: [], fieldMappings: fields.map(createUnconfiguredFieldMapping) }, next)
    }
    if (entry.value.draft.fieldMappings.some((item) => item.mappingType !== 'unconfigured') || Boolean(entry.value.draft.databaseQuery?.items.length) || Boolean(entry.value.draft.databaseWrites?.length) || Boolean(entry.value.draft.externalApiBindings?.length)) confirmWorkspaceAction({ title: '更换数据来源？', content: '当前字段映射、写入字段、外部接口取值规则和数据库查询条件将清空，已保存的正式映射在重新确认前不变。', okText: '更换并清空', cancelText: '取消', onOk: apply })
    else apply()
  }

  /** 进入数据来源更换态，保留当前来源直至用户明确确认。 */
  const beginSourceChange = (): void => {
    setPendingSelection(selection || null)
    setKind(selection?.sourceType || kind)
    setSourceChanging(true)
  }

  /** 取消数据来源更换并恢复当前正式选择。 */
  const cancelSourceChange = (): void => {
    setPendingSelection(null)
    setKind(selection?.sourceType)
    setSourceChanging(false)
  }

  /** 只在用户尝试确认时显示未完成字段的错误，并阻止无效提交。 */
  const confirmMapping = (): void => {
    if (!entry) return
    // 每次提交生成独立校验结果，手动收起后再次提交仍能定位错误。
    setValidation({ draft: entry.value.draft, errors: validationErrors })
    if (Object.keys(validationErrors).length) return
    void state.save(true)
  }

  /** 暂存新的来源类型，等待用户选择具体来源并确认。 */
  const stageSourceKind = (nextKind: BindingSelection['sourceType']): void => {
    if (!selection && !sourceChanging) {
      changeSelection(nextKind === 'static' ? { sourceType: 'static' } : null, nextKind)
      return
    }
    setKind(nextKind)
    setPendingSelection(nextKind === 'static' ? { sourceType: 'static' } : selection?.sourceType === nextKind ? selection : null)
    setSourceChanging(true)
  }

  /** 暂存来源候选；已有来源时不立即清空当前映射。 */
  const stageSourceSelection = (next: BindingSelection | null): void => {
    if (!selection && !sourceChanging) {
      changeSelection(next)
      return
    }
    setPendingSelection(next)
    setSourceChanging(true)
  }

  const pendingSelectionChanged = Boolean(sourceChanging && pendingSelection && selectionKey(pendingSelection) !== selectionKey(selection))
  const displayedSelection = sourceChanging ? pendingSelection : selection

  return <RuleEditingContext.Provider value={trackRuleEditing}><div className="binding-workspace">
    <nav className="binding-directory" aria-label="应用 API"><h4>应用 API</h4>{contracts.map((contract) => <section className="binding-contract" key={contract.id}>
      <div className="binding-contract-heading"><CaretDownOutlined /><FolderOpenOutlined /><strong>{contract.name || '未命名接口分组'}</strong></div>
      <div className="binding-endpoint-list">{contract.endpoints.map((endpoint) => {
        const endpointPath = endpoint.path || '/'; const displayPath = apiEndpointDisplayPath(endpointPath, contract.label); const endpointName = endpoint.name || displayPath || '未命名接口'; const apiContractId = endpoint.apiContractId || contract.id; const selected = target?.apiContractId === apiContractId && target.endpointId === endpoint.id
        return <Tooltip align={{ offset: [4, 0] }} key={endpoint.id} mouseEnterDelay={1} mouseLeaveDelay={0.08} overlayClassName={cx('api-hover-tooltip')} placement="right" title={<span className={cx('api-hover-tooltip-content')}><span className={cx('api-hover-tooltip-method')}>{endpoint.method}</span><code>{endpointPath}</code></span>}><span className={cx('api-tooltip-anchor')}><button aria-current={selected ? 'true' : undefined} aria-label={`${contract.name || '未命名接口分组'}，${endpointName}，${endpoint.method} ${endpointPath}`} className={selected ? 'selected' : ''} disabled={busy} onClick={() => onSelect({ apiContractId, endpointId: endpoint.id, label: endpointName })}><ApiOutlined className="binding-endpoint-icon" /><span className="binding-endpoint-copy"><strong>{endpointName}</strong></span></button></span></Tooltip>
      })}</div>
    </section>)}</nav>
    <main className="binding-editor">
      {!target ? <Empty description="请选择应用 API" /> : loading && !entry ? <Spin tip="正在读取 API 契约…" /> : null}
      {error ? <Alert type="error" showIcon message={error} action={<Space><Button onClick={state.refreshSources}>重试</Button>{entry && <Button disabled={busy} onClick={() => confirmWorkspaceAction({ title: '放弃草稿并重新加载？', content: '未确认的修改将被清除，正式映射不变。', okText: '放弃并加载', cancelText: '继续编辑', onOk: state.discard })}>重新加载</Button>}</Space>} /> : null}
      {entry && <>
        <header className="binding-heading"><div className="binding-heading-copy"><span className="binding-heading-contract">{activeContractName}</span><strong className="binding-heading-name">{activeEndpointName}</strong><div className="binding-heading-endpoint"><Tag>{String(entry.preparation.payload.endpoint.method || 'API')}</Tag><code>{activeEndpointPath}</code></div></div><Space className="binding-heading-actions">{entry.readOnly ? <Button className="binding-edit-button" icon={<EditOutlined />} onClick={() => state.update({ ...entry, readOnly: false })}>修改映射</Button> : !entry.complex ? <><Button className="binding-stash-button" loading={busy} disabled={entry.conflict || editingRules > 0} onClick={() => void state.save(false)}>暂存</Button><Button className="binding-confirm-button" type="primary" loading={busy} disabled={!canConfirm} onClick={confirmMapping}>保存并确认</Button></> : null}</Space></header>
        {entry.conflict ? <Alert type="warning" message="草稿基于的契约或正式映射已变化，当前草稿已保留。" description="请核对当前内容后，明确放弃旧草稿并重新加载。" action={<Button disabled={busy} onClick={() => confirmWorkspaceAction({ title: '放弃旧草稿并重新加载？', okText: '放弃并加载', cancelText: '保留草稿', onOk: state.discard })}>重新加载</Button>} /> : null}
        {entry.complex ? <Alert type="info" message="当前接口包含复杂映射，继续使用原有编辑能力。" action={<Button onClick={() => setAdvanced(true)}>打开完整编辑器</Button>} /> : <>
          {entry.readOnly && selection && <section className="binding-section database-source-section">
            <div className="database-section-heading"><div className="database-section-title is-card"><h4>数据来源</h4></div></div>
            <div className="database-section-content"><div className="database-source-summary">
              <div><Tag>{selection.sourceType === 'database' ? '数据表' : selection.sourceType === 'static' ? '静态数据' : '外部 API'}</Tag><strong>{sourceLabel}</strong></div>
            </div></div>
          </section>}
          {!entry.readOnly && <section className={`binding-section database-source-section${sourceCollapsed ? ' is-collapsed' : ''}`}>
            <div className="database-section-heading">
              <div className="database-section-title is-card"><h4>数据来源</h4></div>
              <Button className="database-section-toggle" type="text" aria-expanded={!sourceCollapsed} aria-label={`${sourceCollapsed ? '展开' : '收起'}数据来源`} icon={sourceCollapsed ? <DownOutlined /> : <UpOutlined />} onClick={() => setSourceCollapsed((collapsed) => !collapsed)} />
            </div>
            {!sourceCollapsed ? <div className="database-section-content">
              {selection && !sourceChanging ? <div className="database-source-summary">
                <div><Tag>{selection.sourceType === 'database' ? '数据表' : selection.sourceType === 'static' ? '静态数据' : '外部 API'}</Tag><strong>{sourceLabel}</strong></div>
                <Button disabled={busy || entry.conflict || editingRules > 0} onClick={beginSourceChange}>更换数据源</Button>
              </div> : <>
                <Radio.Group disabled={busy || entry.conflict} value={kind} onChange={(event) => stageSourceKind(event.target.value)}>
                  <Radio.Button value="database">数据表</Radio.Button>
                  <Radio.Button value="external_api">外部 API</Radio.Button>
                  <Radio.Button value="static">静态数据</Radio.Button>
                </Radio.Group>
                {kind && kind !== 'static' && <Select className="binding-target" showSearch optionFilterProp="label" placeholder={kind === 'database' ? '选择已添加的数据表' : '选择域名 / 目录 / 接口'} disabled={busy || entry.conflict} value={displayedSelection?.sourceType === kind ? selectionKey(displayedSelection) : undefined} options={candidates} onChange={(value) => stageSourceSelection(candidates.find((item) => item.value === value)?.selection || null)} />}
                {sourceChanging ? <div className="database-source-toolbar"><Space><Button disabled={busy} onClick={cancelSourceChange}>取消</Button><Button type="primary" disabled={busy || entry.conflict || !pendingSelectionChanged} onClick={() => changeSelection(pendingSelection, kind)}>确认更换</Button></Space></div> : null}
                {kind && kind !== 'static' && !candidates.length ? <p>暂无可绑定来源，请先配置{kind === 'database' ? '连接并添加数据表' : '外部 API 接口'}。</p> : null}
              </>}
              {unavailableTable ? <Alert type="warning" message="所选表不在已添加清单中，请先添加后再确认。" /> : null}
              {missingQueryColumns ? <Alert type="warning" message="查询条件中的数据库列已失效，请重新选择当前数据表中的列。" /> : null}
            </div> : null}
          </section>}
          {selection?.sourceType === 'static' && <StaticDataCard key={`data:${state.key}`} draft={entry.value.draft} readOnly={entry.readOnly} disabled={!editable || busy || sourceChanging} errors={errors} onChange={state.edit} />}
          {selection?.sourceType === 'static' && <StaticMapping key={state.key} draft={entry.value.draft} fields={fields} readOnly={entry.readOnly} disabled={!editable || busy} previewDisabled={editingRules > 0} errors={errors} onChange={state.edit} />}
          {selection && <Spin spinning={metadataLoading}>{selection.sourceType === 'external_api' && metadata && 'fields' in metadata ? <ExternalMapping key={`${state.key}:${selectionKey(selection)}`} busy={busy} draft={entry.value.draft} editable={editable} readOnly={entry.readOnly} errors={errors} fields={fields} metadata={metadata} onChange={(draft) => state.edit(draft)} selection={selection} /> : databaseSelection && metadata && 'columns' in metadata ? <DatabaseMapping key={`${state.key}:${selectionKey(selection)}:${entry.value.draft.databaseOperation}`} busy={busy} draft={entry.value.draft} editable={editable} readOnly={entry.readOnly} errors={errors} fields={fields} metadata={metadata} operation={entry.value.draft.databaseOperation} selection={databaseSelection} onChange={(draft) => state.edit(draft)} onOperationChange={(operation) => confirmWorkspaceAction({ title: '切换数据库操作？', content: '切到新增会清空查询条件；切到查询或删除会清除写入映射。', okText: '切换并更新', cancelText: '取消', onOk: () => state.edit(resetDraftForDatabaseOperation(entry.value.draft, operation)) })} /> : null}</Spin>}
          {entry.readOnly && <p className="binding-readonly"><NodeIndexOutlined /> 映射已确认</p>}
        </>}
      </>}
    </main>
    <ApiDesignConfigModal open={advanced} target={target} workspaceRoot={workspaceRoot} onClose={() => setAdvanced(false)} onSaved={async (current, result) => { await onSaved(current, result); state.reload() }} />
  </div></RuleEditingContext.Provider>
}
