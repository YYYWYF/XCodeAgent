import { requestDataSources, requestSelectedTables } from '../../../../service/dataSources'
import type { SelectedDataTable } from '../../../../service/dataSources'
import { subscribeDataSourcesChanged } from '../../../../service/dataSourceEvents'
import type { DataSourceCatalog } from '../../../../typings'

type BindingSourcesSnapshot = { catalog: DataSourceCatalog; tables: SelectedDataTable[] }
export type BindingSourcesReader = { refresh: () => void; dispose: () => void }

/** 只读刷新来源候选，来源变更不得重新加载或替换接口草稿。 */
export function observeBindingSources(
  workspaceRoot: string,
  onSnapshot: (snapshot: BindingSourcesSnapshot) => void,
  onError: (reason: unknown) => void
): BindingSourcesReader {
  let disposed = false
  let revision = 0

  /** 每次刷新使旧请求失效，防止增删后的清单被迟到的旧快照覆盖。 */
  const refresh = (): void => {
    if (disposed) return
    const requestRevision = ++revision
    void Promise.all([requestDataSources(workspaceRoot), requestSelectedTables(workspaceRoot)])
      .then(([catalog, tables]) => {
        if (!disposed && requestRevision === revision) onSnapshot({ catalog, tables })
      })
      .catch((reason: unknown) => {
        if (!disposed && requestRevision === revision) onError(reason)
      })
  }
  const unsubscribe = subscribeDataSourcesChanged(workspaceRoot, refresh)
  refresh()
  return {
    refresh,
    /** 关闭工作区时忽略全部在途响应并解除订阅。 */
    dispose: (): void => {
      disposed = true
      revision += 1
      unsubscribe()
    }
  }
}
