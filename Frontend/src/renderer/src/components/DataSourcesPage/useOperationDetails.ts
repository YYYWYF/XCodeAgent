import { useEffect, useState } from 'react'
import { requestDataSourceOperation } from '../../service/dataSources'
import type { DataSourceCatalog, DataSourceOperation } from '../../typings/dataSources'
import { requireOperationDetails } from './dataSourceOperations'

type Props = {
  workspaceRoot: string
  sourceId: string
  operationId: string
  open: boolean
  catalog?: DataSourceCatalog
}

type DetailState = {
  key: string
  catalog?: DataSourceCatalog
  attempt: number
  operation?: DataSourceOperation
  error?: string
}

/** 按当前选中接口独立读取详情；摘要刷新、切换接口或关闭弹窗后忽略过期响应。 */
export function useOperationDetails({ workspaceRoot, sourceId, operationId, open, catalog }: Props): {
  operation?: DataSourceOperation
  loading: boolean
  error: string
  retry: () => void
} {
  const key = open && sourceId && operationId ? JSON.stringify([workspaceRoot, sourceId, operationId]) : ''
  const [attempt, setAttempt] = useState(0)
  const [state, setState] = useState<DetailState>()

  useEffect(() => {
    if (!key) {
      setState(undefined)
      return
    }
    let active = true
    /** 详情只进入局部状态，不把本次响应中的其他接口摘要写回主目录。 */
    const load = async (): Promise<void> => {
      try {
        const response = await requestDataSourceOperation(workspaceRoot, sourceId, operationId)
        const operation = requireOperationDetails(response, sourceId, operationId)
        if (active) setState({ key, catalog, attempt, operation })
      } catch (caughtError) {
        if (active) setState({ key, catalog, attempt, error: caughtError instanceof Error ? caughtError.message : '接口详情读取失败。' })
      }
    }
    void load()
    return () => { active = false }
  }, [attempt, catalog, key, operationId, sourceId, workspaceRoot])

  // 在 effect 执行前也先校验目标与目录引用，避免切换时短暂显示上一接口或旧详情。
  const current = key && state?.key === key && state.catalog === catalog && state.attempt === attempt ? state : undefined
  /** 仅重试当前详情，不重新读取整个目录。 */
  const retry = (): void => { setAttempt((value) => value + 1) }
  return { operation: current?.operation, loading: Boolean(key && !current), error: current?.error || '', retry }
}
