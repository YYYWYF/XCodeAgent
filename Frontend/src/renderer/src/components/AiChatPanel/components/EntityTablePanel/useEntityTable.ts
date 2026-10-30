import { useEffect, useRef, useState } from 'react'
import { EntityTableActionError, requestEntityTable } from '../../../../service/entityTables'
import type { EntityTableIssue, EntityTablePayload } from '../../../../service/entityTables'
import { requestApiDesignDatabaseColumns } from '../../../../service/dataSources'
import type { ApiDesignDatabaseMetadata } from '../../../../service/dataSources'
import { notifyDataSourcesChanged } from '../../../../service/dataSourceEvents'

export type EntityTableColumn = NonNullable<ApiDesignDatabaseMetadata['columns']>[number]
export type EntityTableAction = 'prepare' | 'execute' | 'rebuild' | 'refresh'

/** 管理实体建表、明确确认与只读检查，字段仅来自已存在表的实时元数据。 */
export default function useEntityTable(workspaceRoot: string, entityId: string) {
  const [data, setData] = useState<EntityTablePayload>()
  const [columns, setColumns] = useState<EntityTableColumn[]>([])
  const [columnsError, setColumnsError] = useState('')
  const [busyAction, setBusyAction] = useState<EntityTableAction | undefined>('prepare')
  const [error, setError] = useState('')
  const [issue, setIssue] = useState<EntityTableIssue>()
  const [confirming, setConfirming] = useState<'execute' | 'rebuild'>()
  const [allowDataLoss, setAllowDataLoss] = useState(false)
  const [mustRefresh, setMustRefresh] = useState(false)
  const [autoRefreshScheduled, setAutoRefreshScheduled] = useState(false)
  const active = useRef(false)
  const pending = useRef(false)
  const generation = useRef(0)
  const autoRefreshTimer = useRef<number>()
  const notifiedOperation = useRef<string>()

  useEffect(() => {
    active.current = true
    // 页签首次打开只生成设计并读取实际状态，不提交任何建表动作。
    void runAction('prepare')
    return () => {
      active.current = false
      generation.current += 1
      pending.current = false
      cancelAutoRefresh()
    }
  }, [])

  /** 清理单次查询定时器，切换页签保留组件，切换实体或卸载后停止更新。 */
  function cancelAutoRefresh(): void {
    if (autoRefreshTimer.current !== undefined) {
      window.clearTimeout(autoRefreshTimer.current)
      autoRefreshTimer.current = undefined
    }
    if (active.current) setAutoRefreshScheduled(false)
  }

  /** 运行现有 AG-UI 动作，受理不视为成功，运行中及未知结果禁止再次提交。 */
  async function runAction(action: EntityTableAction): Promise<void> {
    if (pending.current || (action === 'rebuild' && !allowDataLoss)) return
    cancelAutoRefresh()
    const requestId = ++generation.current
    const submitting = action === 'execute' || action === 'rebuild'
    pending.current = true
    setBusyAction(action)
    setError('')
    setIssue(undefined)
    setColumns([])
    setColumnsError('')
    setConfirming(undefined)
    setAllowDataLoss(false)
    // 每次查询或提交都撤销旧结构证据，失败后不把上次字段当成本次真实结构。
    setData((current) => current ? { ...current, readiness: undefined } : current)
    if (submitting) setMustRefresh(true)
    try {
      let result = await requestEntityTable(
        workspaceRoot, entityId, action, data?.preview?.previewHash, allowDataLoss
      )
      if (!active.current || requestId !== generation.current) return
      if (action === 'prepare') {
        // 先保留当前表名；即使远端检查失败，也能显示设计和明确的刷新入口。
        setData(result)
        result = await requestEntityTable(workspaceRoot, entityId, 'refresh')
        if (!active.current || requestId !== generation.current) return
      }
      setData(result)
      if (submitting && result.receipt?.status === 'RUNNING') {
        setAutoRefreshScheduled(true)
        autoRefreshTimer.current = window.setTimeout(() => {
          autoRefreshTimer.current = undefined
          if (!active.current) return
          setAutoRefreshScheduled(false)
          void runAction('refresh')
        }, 10_000)
      }
      if (!submitting) {
        const unsettled = ['RUNNING', 'UNKNOWN'].includes(result.readiness?.operationStatus || '')
          || ['RUNNING', 'UNKNOWN'].includes(result.operation?.status || '')
        setMustRefresh(unsettled)
        const operation = result.operation
        if (operation && ['SUCCEEDED', 'FAILED'].includes(operation.status)
          && notifiedOperation.current !== operation.operationId) {
          notifiedOperation.current = operation.operationId
          notifyDataSourcesChanged(workspaceRoot)
        }
        const preview = result.preview
        const table = result.readiness?.tables.find((item) => item.tableName === preview?.tableName)
        // 未建表、归属冲突或结果未知时不查询字段，不能用 preview.columns 冒充实际列。
        if (preview && !unsettled && table && ['SYNCED', 'STRUCTURE_MISMATCH'].includes(table.status)) {
          try {
            const metadata = await requestApiDesignDatabaseColumns(
              workspaceRoot, 'application-database', preview.tableName
            )
            if (!active.current || requestId !== generation.current) return
            if (metadata.table !== preview.tableName || metadata.managedDatasourceKey !== preview.datasourceKey
              || !metadata.columns?.length) throw new Error('未读取到当前模拟数据表的有效字段。')
            setColumns(metadata.columns)
          } catch (caught) {
            if (active.current && requestId === generation.current) {
              setColumnsError(caught instanceof Error ? caught.message : '读取数据表字段失败。')
            }
          }
        }
      }
    } catch (caught) {
      if (active.current && requestId === generation.current) {
        setError(caught instanceof Error ? caught.message : '实体建表操作失败。')
        if (caught instanceof EntityTableActionError) {
          setIssue(caught.issue)
          // 只有明确拒绝的结构冲突可进入重建确认，网络异常仍需先核对结果。
          if (action === 'execute' && caught.issue.action === 'rebuild') setMustRefresh(false)
        }
      }
    } finally {
      if (requestId === generation.current) {
        pending.current = false
        if (active.current) setBusyAction(undefined)
      }
    }
  }

  /** 每次重新确认都清空丢弃数据授权，避免上次勾选被复用。 */
  function beginConfirmation(action: 'execute' | 'rebuild'): void {
    setAllowDataLoss(false)
    setConfirming(action)
  }

  /** 取消确认时同时撤销本次的数据清空授权。 */
  function cancelConfirmation(): void {
    setConfirming(undefined)
    setAllowDataLoss(false)
  }

  return {
    data, columns, columnsError, error, issue, busyAction,
    loading: busyAction !== undefined,
    confirming, allowDataLoss, mustRefresh, autoRefreshScheduled,
    setAllowDataLoss, beginConfirmation, cancelConfirmation, runAction
  }
}
