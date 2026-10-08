import { useCallback, useEffect, useRef, useState } from 'react'
import { message } from 'antd'
import type { BindingDraft } from '../../../../typings/endpointDesign'
import type { EndpointDesignPreparation, EndpointDesignSaveResult, WorkflowApiDesignDraft, DataSourceCatalog } from '../../../../typings'
import type { ApiDesignConfigTarget } from '../WorkflowRunCard/ApiDesignConfigModal'
import { discardEndpointBindingDraft, requestEndpointDesignPreparation, saveEndpointBindingDraft, saveEndpointDesign } from '../../../../service/endpointDesigns'
import { requestSelectedTables, requestApiDesignDatabaseColumns, requestApiDesignExternalOperation } from '../../../../service/dataSources'
import type { SelectedDataTable, ApiDesignDatabaseMetadata, ApiDesignExternalOperationMetadata } from '../../../../service/dataSources'
import { defaultDatabaseOperation, normalizeApiDesignDraft, validateApiDesignDraft } from '../WorkflowRunCard/apiDesignSerialization'
import { inferSelection, selectionKey, tableIsSelected } from './model'
import { AgUiBusinessError } from '../../../../service/agUiBusinessError'
import { BindingInputError, readBindingRecoverySnapshot, reconcileBindingAfterRecovery } from './bindingRecovery'
import { endpointRecoveryScope, type EndpointRecoveryReporter } from '../../hooks/useEndpointDesignRecovery'
import { observeBindingSources, type BindingSourcesReader } from './bindingSources'

export type BindingEntry = {
  preparation: EndpointDesignPreparation; value: BindingDraft; readOnly: boolean; complex: boolean; conflict: boolean; dirty: boolean
}

type BindingWorkspaceState = {
  key: string
  entry?: BindingEntry
  catalog: DataSourceCatalog
  tables: SelectedDataTable[]
  loading: boolean
  busy: boolean
  error: string
  reportedError: boolean
  metadata?: ApiDesignDatabaseMetadata | ApiDesignExternalOperationMetadata
  metadataLoading: boolean
  update: (next: BindingEntry) => void
  edit: (draft: WorkflowApiDesignDraft, selection?: BindingDraft['selection']) => void
  save: (confirm: boolean) => Promise<void>
  reload: () => void
  discard: () => Promise<void>
  refreshSources: () => void
  refreshSourceCandidates: () => void
}

/** 为工作台缓存每个接口的编辑状态，切页签与异步返回均不覆盖其他接口。 */
export function useBindingWorkspace(workspaceRoot: string, target: ApiDesignConfigTarget | undefined,
  onSaved: (target: ApiDesignConfigTarget, result: EndpointDesignSaveResult) => void | Promise<void>,
  onRecovery?: EndpointRecoveryReporter): BindingWorkspaceState {
  const [entries, setEntries] = useState<Record<string, BindingEntry>>({})
  const [catalog, setCatalog] = useState<DataSourceCatalog>({ sources: [] })
  const [tables, setTables] = useState<SelectedDataTable[]>([])
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [reportedError, setReportedError] = useState(false)
  const [metadata, setMetadata] = useState<ApiDesignDatabaseMetadata | ApiDesignExternalOperationMetadata>()
  const [metadataLoading, setMetadataLoading] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const sourcesReader = useRef<BindingSourcesReader>()
  const locked = useRef(false)
  const generation = useRef(0)
  const key = target ? JSON.stringify([workspaceRoot, target.apiContractId, target.endpointId]) : ''
  const entry = entries[key]
  const sourceKey = selectionKey(entry?.value.selection)
  const activeKey = useRef(key)
  activeKey.current = key
  const entryRef = useRef(entry)
  entryRef.current = entry
  const recoveryRef = useRef(onRecovery)
  recoveryRef.current = onRecovery
  const retryRef = useRef<() => Promise<void>>(async () => {})
  /** 每个失败携带原接口身份，其他并行读取成功不能清除它。 */
  const publish = useCallback((slot: string, reason?: unknown, scopeKey = key): void => {
    recoveryRef.current?.({ workspaceRoot, scopeKey, source: 'binding', slot, reason, retry: () => retryRef.current() })
  }, [workspaceRoot, key])
  /** 读取失败保留当前输入，并交给底部独立入口。 */
  const failRead = (reason: unknown, slot: string, scopeKey = key): void => {
    setError(reason instanceof Error ? reason.message : String(reason))
    setReportedError(true)
    publish(slot, reason, scopeKey)
  }

  /** 只替换明确身份的条目，异步操作不会写入当前另一接口。 */
  const update = useCallback((next: BindingEntry) => { setEntries((all) => ({ ...all, [key]: next })) }, [key])

  useEffect(() => {
    generation.current += 1
    setEntries({}); setCatalog({ sources: [] }); setTables([])
    return () => { generation.current += 1 }
  }, [workspaceRoot])

  useEffect(() => {
    setError('')
    const reader = observeBindingSources(workspaceRoot, ({ catalog: sources, tables: selected }) => {
      setCatalog(sources); setTables(selected); publish('catalog', undefined, endpointRecoveryScope(workspaceRoot))
    }, (reason) => failRead(reason, 'catalog', endpointRecoveryScope(workspaceRoot)))
    sourcesReader.current = reader
    return () => { reader.dispose(); sourcesReader.current = undefined }
  }, [workspaceRoot, refresh])

  useEffect(() => {
    if (!target || entries[key]) return
    let disposed = false
    setLoading(true); setError('')
    requestEndpointDesignPreparation(workspaceRoot, target.apiContractId, target.endpointId).then((preparation) => {
      if (disposed) return
      publish('prepare')
      let draft = normalizeApiDesignDraft(preparation.payload)
      const inferred = inferSelection(draft)
      // 数据库来源首次进入工作台时按 HTTP 方法初始化 CRUD；外部 API 不携带数据库操作。
      if (inferred.selection?.sourceType === 'database' && !draft.databaseOperation) {
        draft = { ...draft, databaseOperation: defaultDatabaseOperation(String(preparation.payload.endpoint?.method || '')) }
      }
      const saved = preparation.bindingDraft
      const conflict = Boolean(saved && (saved.baseRevision !== (preparation.artifactRevision || null) || saved.technicalPlanHash !== preparation.technicalPlanHash))
      update({ preparation, value: saved || { draft, selection: inferred.selection, baseRevision: preparation.artifactRevision || null,
        technicalPlanHash: preparation.technicalPlanHash }, readOnly: !saved && preparation.payload.existingStatus?.status === 'confirmed',
        complex: inferred.complex, conflict, dirty: false })
    }).catch((reason) => { if (!disposed) failRead(reason, 'prepare') })
      .finally(() => { if (!disposed) setLoading(false) })
    return () => { disposed = true }
  }, [key, workspaceRoot, target?.apiContractId, target?.endpointId, refresh])

  useEffect(() => {
    const selection = entry?.value.selection
    setMetadata(undefined)
    if (!selection || selection.sourceType === 'static') { setMetadataLoading(false); publish('metadata'); return }
    let disposed = false
    setMetadataLoading(true); setError('')
    const request = selection.sourceType === 'database'
      ? requestApiDesignDatabaseColumns(workspaceRoot, selection.sourceId, selection.table)
      : requestApiDesignExternalOperation(workspaceRoot, selection.sourceId, selection.directoryId, selection.operationId)
    request.then((value) => { if (!disposed) { setMetadata(value); publish('metadata') } })
      .catch((reason) => { if (!disposed) failRead(reason, 'metadata') })
      .finally(() => { if (!disposed) setMetadataLoading(false) })
    return () => { disposed = true }
  }, [workspaceRoot, key, sourceKey, refresh])

  /** 底部重试仅校准来源和当前契约；不调用会删除草稿的 reload，也不重放保存或确认。 */
  const recover = async (): Promise<void> => {
    if (locked.current) return
    locked.current = true; setBusy(true)
    const requestGeneration = generation.current
    const capturedKey = key
    try {
      const selection = entryRef.current?.value.selection
      const [sources, selected, preparation, nextMetadata] = await readBindingRecoverySnapshot(workspaceRoot, target, selection)
      if (generation.current !== requestGeneration || activeKey.current !== capturedKey) return
      setCatalog(sources); setTables(selected); setMetadata(nextMetadata)
      const current = entryRef.current
      if (current && preparation) update(reconcileBindingAfterRecovery(current, preparation))
      else setRefresh((value) => value + 1)
      setError(''); setReportedError(false)
      publish('*'); publish('catalog', undefined, endpointRecoveryScope(workspaceRoot))
    } catch (reason) {
      if (generation.current === requestGeneration && activeKey.current === capturedKey) failRead(reason, 'recovery')
      throw reason
    } finally { locked.current = false; setBusy(false) }
  }
  retryRef.current = recover

  /** 更新内存草稿，正式确认状态直到明确提交才发生变化。 */
  const edit = (draft: WorkflowApiDesignDraft, selection = entry?.value.selection): void => {
    if (entry) update({ ...entry, readOnly: false, dirty: true, value: { ...entry.value, draft, selection: selection || null } })
  }

  /** 保存草稿或复用正式确认动作，并锁定重复点击。 */
  const save = async (confirm: boolean): Promise<void> => {
    if (!entry || !target || locked.current || entry.conflict) return
    locked.current = true; setBusy(true); setError('')
    const requestGeneration = generation.current
    let saved = false
    try {
      if (confirm) {
        const selection = entry.value.selection
        const hasFields = Boolean(entry.preparation.payload.endpointFields?.length)
        // 空字段确认保留原契约；已有来源必须由用户主动清除，不能提交时静默丢弃。
        if (!hasFields && (selection || entry.value.draft.fieldMappings.length)) throw new BindingInputError('当前接口没有可映射字段，请先清除来源选择再确认；也可保留并保存草稿。')
        if (hasFields) {
          if (!selection) throw new BindingInputError('请选择数据来源。')
          if (selection.sourceType === 'database' && !tableIsSelected(selection, await requestSelectedTables(workspaceRoot))) throw new BindingInputError('请先在数据来源中添加所选数据表。')
        }
        const errors = validateApiDesignDraft(entry.value.draft)
        if (Object.keys(errors).length) throw new BindingInputError('配置尚未完成，请检查标红的项目。')
        const fresh = await requestEndpointDesignPreparation(workspaceRoot, target.apiContractId, target.endpointId)
        if (fresh.technicalPlanHash !== entry.value.technicalPlanHash || (fresh.artifactRevision || null) !== entry.value.baseRevision) throw new BindingInputError('契约或正式映射已变化，请重新加载；当前输入仍保留。')
        const result = await saveEndpointDesign(workspaceRoot, { action: 'confirm', apiContractId: target.apiContractId, endpointId: target.endpointId, draft: entry.value.draft }, entry.value.baseRevision,
          { bindingSelection: selection || undefined, technicalPlanHash: entry.value.technicalPlanHash })
        if (generation.current !== requestGeneration) return
        update({ ...entry, dirty: false, readOnly: true, value: { ...entry.value, baseRevision: result.artifactRevision } })
        saved = true
        await onSaved(target, result)
        message.success('映射已确认，开发仍需在门禁中确认继续。')
      } else {
        const value = await saveEndpointBindingDraft(workspaceRoot, entry.value)
        if (generation.current !== requestGeneration) return
        update({ ...entry, value, dirty: false }); message.success('草稿已保存')
      }
      publish('*')
    } catch (reason) {
      if (generation.current === requestGeneration && activeKey.current === key) {
        setError(reason instanceof Error ? reason.message : '保存失败。')
        setReportedError(!(reason instanceof BindingInputError))
      }
      if (generation.current === requestGeneration && !(reason instanceof BindingInputError)) publish('save', saved ? new AgUiBusinessError('映射已保存，但本地门禁刷新失败，请同步状态。') : reason)
    }
    finally { locked.current = false; setBusy(false) }
  }

  /** 明确重新加载时放弃当前接口内存草稿，其余接口不受影响。 */
  const reload = (): void => { setEntries((all) => { const next = { ...all }; delete next[key]; return next }); setRefresh((value) => value + 1) }
  /** 明确放弃冲突草稿后从当前正式映射重新开始。 */
  const discard = async (): Promise<void> => {
    if (!target || locked.current) return
    setBusy(true); locked.current = true
    const requestGeneration = generation.current
    try { await discardEndpointBindingDraft(workspaceRoot, target.apiContractId, target.endpointId); reload() }
    catch (reason) { if (generation.current === requestGeneration && activeKey.current === key) failRead(reason, 'discard') }
    finally { setBusy(false); locked.current = false }
  }
  return { key, entry, catalog, tables, loading, busy, error, reportedError, metadata, metadataLoading, update, edit, save, reload,
    discard,
    refreshSources: () => setRefresh((value) => value + 1),
    refreshSourceCandidates: () => sourcesReader.current?.refresh() }
}
