import { AgUiBusinessError } from '../../../../service/agUiBusinessError'
import { requestEndpointDesignPreparation } from '../../../../service/endpointDesigns'
import { requestDataSources, requestSelectedTables, requestApiDesignDatabaseColumns, requestApiDesignExternalOperation } from '../../../../service/dataSources'
import type { BindingSelection } from '../../../../typings/endpointDesign'
import type { EndpointDesignPreparation } from '../../../../typings'
import type { BindingEntry } from './useBindingWorkspace'

/** 保持原表单校验在编辑区，不把未提交的输入问题当作传输异常。 */
export class BindingInputError extends AgUiBusinessError {}

/** 只比较服务端版本并保留内存草稿，断线校准不替换输入或伪造保存确认。 */
export function reconcileBindingAfterRecovery(entry: BindingEntry, preparation: EndpointDesignPreparation): BindingEntry {
  return { ...entry, conflict: entry.conflict || entry.value.baseRevision !== (preparation.artifactRevision || null) ||
    entry.value.technicalPlanHash !== preparation.technicalPlanHash }
}

/** 校准只调用现有 AG-UI 读取动作，不保存、丢弃草稿或推进开发。 */
export async function readBindingRecoverySnapshot(workspaceRoot: string,
  target?: { apiContractId: string; endpointId: string }, selection?: BindingSelection | null) {
  const metadataRequest = selection?.sourceType === 'database'
    ? requestApiDesignDatabaseColumns(workspaceRoot, selection.sourceId, selection.table)
    : selection?.sourceType === 'external_api'
      ? requestApiDesignExternalOperation(workspaceRoot, selection.sourceId, selection.directoryId, selection.operationId)
      : Promise.resolve(undefined)
  return Promise.all([requestDataSources(workspaceRoot), requestSelectedTables(workspaceRoot),
    target ? requestEndpointDesignPreparation(workspaceRoot, target.apiContractId, target.endpointId) : Promise.resolve(undefined), metadataRequest])
}
