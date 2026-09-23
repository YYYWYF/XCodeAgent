import { useCallback, useEffect, useRef, useState } from 'react'
import { message } from 'antd'
import type { BindingDraft } from '../../../../typings/endpointDesign'
import type { EndpointDesignPreparation, EndpointDesignSaveResult, WorkflowApiDesignDraft, DataSourceCatalog } from '../../../../typings'
import type { ApiDesignConfigTarget } from '../WorkflowRunCard/ApiDesignConfigModal'
import { discardEndpointBindingDraft, requestEndpointDesignPreparation, saveEndpointBindingDraft, saveEndpointDesign } from '../../../../service/endpointDesigns'
import { requestDataSources, requestSelectedTables, requestApiDesignDatabaseColumns, requestApiDesignExternalOperation } from '../../../../service/dataSources'
import type { SelectedDataTable, ApiDesignDatabaseMetadata, ApiDesignExternalOperationMetadata } from '../../../../service/dataSources'
import { defaultDatabaseOperation, normalizeApiDesignDraft, validateApiDesignDraft } from '../WorkflowRunCard/apiDesignSerialization'
import { inferSelection, selectionKey, tableIsSelected } from './model'

export type BindingEntry = {
  preparation: EndpointDesignPreparation; value: BindingDraft; readOnly: boolean; complex: boolean; conflict: boolean; dirty: boolean
}

/** 为工作台缓存每个接口的编辑状态，切页签与异步返回均不覆盖其他接口。 */
export function useBindingWorkspace(workspaceRoot: string, target: ApiDesignConfigTarget | undefined,
  onSaved: (target: ApiDesignConfigTarget, result: EndpointDesignSaveResult) => void | Promise<void>) {
  const [entries, setEntries] = useState<Record<string, BindingEntry>>({})
  const [catalog, setCatalog] = useState<DataSourceCatalog>({ sources: [] })
  const [tables, setTables] = useState<SelectedDataTable[]>([])
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [metadata, setMetadata] = useState<ApiDesignDatabaseMetadata | ApiDesignExternalOperationMetadata>()
  const [metadataLoading, setMetadataLoading] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const locked = useRef(false)
  const generation = useRef(0)
  const key = target ? JSON.stringify([workspaceRoot, target.apiContractId, target.endpointId]) : ''
  const entry = entries[key]
  const sourceKey = selectionKey(entry?.value.selection)

  /** 只替换明确身份的条目，异步操作不会写入当前另一接口。 */
  const update = useCallback((next: BindingEntry) => { setEntries((all) => ({ ...all, [key]: next })) }, [key])

  useEffect(() => {
    generation.current += 1
    setEntries({}); setCatalog({ sources: [] }); setTables([])
    return () => { generation.current += 1 }
  }, [workspaceRoot])

  useEffect(() => {
    let disposed = false
    setError('')
    Promise.all([requestDataSources(workspaceRoot), requestSelectedTables(workspaceRoot)])
      .then(([sources, selected]) => { if (!disposed) { setCatalog(sources); setTables(selected) } })
      .catch((reason) => { if (!disposed) setError(String(reason.message || reason)) })
    return () => { disposed = true }
  }, [workspaceRoot, refresh])

  useEffect(() => {
    if (!target || entries[key]) return
    let disposed = false
    setLoading(true); setError('')
    requestEndpointDesignPreparation(workspaceRoot, target.apiContractId, target.endpointId).then((preparation) => {
      if (disposed) return
      let draft = normalizeApiDesignDraft(preparation.payload)
      const inferred = inferSelection(draft)
      // 数据库来源首次进入工作台时按 HTTP 方法初始化 CRUD；外部 API 不携带数据库操作。
      if (inferred.selection?.sourceType === 'database' && !draft.databaseOperation) {
        draft = { ...draft, databaseOperation: defaultDatabaseOperation(String(preparation.payload.endpoint?.method || '')), databaseConditions: draft.databaseConditions || [] }
      }
      const saved = preparation.bindingDraft
      const conflict = Boolean(saved && (saved.baseRevision !== (preparation.artifactRevision || null) || saved.technicalPlanHash !== preparation.technicalPlanHash))
      update({ preparation, value: saved || { draft, selection: inferred.selection, baseRevision: preparation.artifactRevision || null,
        technicalPlanHash: preparation.technicalPlanHash }, readOnly: !saved && preparation.payload.existingStatus?.status === 'confirmed',
        complex: inferred.complex, conflict, dirty: false })
    }).catch((reason) => { if (!disposed) setError(String(reason.message || reason)) })
      .finally(() => { if (!disposed) setLoading(false) })
    return () => { disposed = true }
  }, [key, workspaceRoot, target?.apiContractId, target?.endpointId, refresh])

  useEffect(() => {
    const selection = entry?.value.selection
    setMetadata(undefined)
    if (!selection) { setMetadataLoading(false); return }
    let disposed = false
    setMetadataLoading(true); setError('')
    const request = selection.sourceType === 'database'
      ? requestApiDesignDatabaseColumns(workspaceRoot, selection.sourceId, selection.table)
      : requestApiDesignExternalOperation(workspaceRoot, selection.sourceId, selection.directoryId, selection.operationId)
    request.then((value) => { if (!disposed) setMetadata(value) })
      .catch((reason) => { if (!disposed) setError(String(reason.message || reason)) })
      .finally(() => { if (!disposed) setMetadataLoading(false) })
    return () => { disposed = true }
  }, [workspaceRoot, key, sourceKey, refresh])

  /** 更新内存草稿，正式确认状态直到明确提交才发生变化。 */
  const edit = (draft: WorkflowApiDesignDraft, selection = entry?.value.selection): void => {
    if (entry) update({ ...entry, readOnly: false, dirty: true, value: { ...entry.value, draft, selection: selection || null } })
  }

  /** 保存草稿或复用正式确认动作，并锁定重复点击。 */
  const save = async (confirm: boolean): Promise<void> => {
    if (!entry || !target || locked.current || entry.conflict) return
    locked.current = true; setBusy(true); setError('')
    const requestGeneration = generation.current
    try {
      if (confirm) {
        const selection = entry.value.selection
        const hasFields = Boolean(entry.preparation.payload.endpointFields?.length)
        // 空字段确认保留原契约；已有来源必须由用户主动清除，不能提交时静默丢弃。
        if (!hasFields && (selection || entry.value.draft.fieldMappings.length)) throw new Error('当前接口没有可映射字段，请先清除来源选择再确认；也可保留并保存草稿。')
        if (hasFields) {
          if (!selection) throw new Error('请选择数据来源。')
          if (selection.sourceType === 'database' && !tableIsSelected(selection, await requestSelectedTables(workspaceRoot))) throw new Error('请先在数据来源中添加所选数据表。')
        }
        const errors = validateApiDesignDraft(entry.value.draft)
        if (Object.keys(errors).length) throw new Error('请完成全部字段映射后再确认。')
        const fresh = await requestEndpointDesignPreparation(workspaceRoot, target.apiContractId, target.endpointId)
        if (fresh.technicalPlanHash !== entry.value.technicalPlanHash || (fresh.artifactRevision || null) !== entry.value.baseRevision) throw new Error('契约或正式映射已变化，请重新加载；当前输入仍保留。')
        const result = await saveEndpointDesign(workspaceRoot, { action: 'confirm', apiContractId: target.apiContractId, endpointId: target.endpointId, draft: entry.value.draft }, entry.value.baseRevision,
          { bindingSelection: selection || undefined, technicalPlanHash: entry.value.technicalPlanHash })
        if (generation.current !== requestGeneration) return
        update({ ...entry, dirty: false, readOnly: true, value: { ...entry.value, baseRevision: result.artifactRevision } })
        await onSaved(target, result)
        message.success('映射已确认，开发仍需在门禁中确认继续。')
      } else {
        const value = await saveEndpointBindingDraft(workspaceRoot, entry.value)
        if (generation.current !== requestGeneration) return
        update({ ...entry, value, dirty: false }); message.success('草稿已保存')
      }
    } catch (reason) { if (generation.current === requestGeneration) setError(reason instanceof Error ? reason.message : '保存失败。') }
    finally { locked.current = false; setBusy(false) }
  }

  /** 明确重新加载时放弃当前接口内存草稿，其余接口不受影响。 */
  const reload = (): void => { setEntries((all) => { const next = { ...all }; delete next[key]; return next }); setRefresh((value) => value + 1) }
  /** 明确放弃冲突草稿后从当前正式映射重新开始。 */
  const discard = async (): Promise<void> => {
    if (!target || locked.current) return
    setBusy(true); locked.current = true
    try { await discardEndpointBindingDraft(workspaceRoot, target.apiContractId, target.endpointId); reload() }
    catch (reason) { setError(reason instanceof Error ? reason.message : '放弃草稿失败。') }
    finally { setBusy(false); locked.current = false }
  }
  return { key, entry, catalog, tables, loading, busy, error, metadata, metadataLoading, update, edit, save, reload,
    discard,
    refreshSources: () => setRefresh((value) => value + 1) }
}
