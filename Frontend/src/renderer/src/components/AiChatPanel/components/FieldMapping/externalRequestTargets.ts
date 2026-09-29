import type { WorkflowApiDesignDraft } from '../../../../typings'
import type { BindingSelection } from '../../../../typings/endpointDesign'

/** 以已保存的绑定决定可选参数是否启用，必填参数始终保留供校验和配置。 */
export function externalTargetEnabled(
  target: { section: string; path: string; required?: boolean },
  selection: BindingSelection,
  bindings: NonNullable<WorkflowApiDesignDraft['externalApiBindings']>
): boolean {
  return Boolean(target.required) || bindings.some(({ externalField: field }) =>
    selection.sourceType === 'external_api' && field.sourceId === selection.sourceId &&
    field.directoryId === selection.directoryId && field.operationId === selection.operationId &&
    field.section === target.section && field.path === target.path)
}
