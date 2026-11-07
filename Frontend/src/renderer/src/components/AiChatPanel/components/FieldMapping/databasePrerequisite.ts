import type { DataSourceCatalog, DatabaseSourceMode, DevelopmentPlanningEntityOption } from '../../../../typings'
import type { SelectedDataTable } from '../../../../service/dataSources'

export type DatabaseGuideState = 'hidden' | 'loading' | 'load_error' | 'builtin_create_table' | 'external_config_connection' | 'external_add_table' | 'ready'
export type DatabaseModeHint = DatabaseSourceMode | 'external'

/** 只定位当前接口关联且仍存在的实体，多实体时必须由用户明确选择。 */
export function resolveDatabaseGuideEntities(entityIds: string[] | undefined, entities: DevelopmentPlanningEntityOption[], selectedId?: string) {
  const relatedEntities = entities.filter((entity) => entityIds?.includes(entity.id))
  const targetEntityId = relatedEntities.length === 1 ? relatedEntities[0].id : relatedEntities.find((entity) => entity.id === selectedId)?.id
  return { relatedEntities, targetEntityId }
}

/** 仅依据应用模式与成功读取的真实目录判断前置条件，失败与空目录不能互换。 */
export function resolveDatabasePrerequisite(input: {
  kind?: string
  mode?: DatabaseModeHint
  catalog: DataSourceCatalog
  tables: SelectedDataTable[]
  loading: boolean
  error: string
}): DatabaseGuideState {
  if (input.kind !== 'database') return 'hidden'
  if (input.loading) return 'loading'
  if (input.error) return 'load_error'
  const sources = input.catalog.sources.filter((source) => source.type === 'database')
  if (sources.length > 1) return 'load_error'
  const source = sources[0]
  const mode = source?.mode || input.mode
  if (!['builtin', 'dbid', 'direct', 'external'].includes(mode || '')) return 'load_error'
  // 应用明确为内置或外部时必须核对目录，不能因表为空猜测模式。
  if (source && input.mode && (input.mode === 'external' ? source.mode === 'builtin' : source.mode !== input.mode)) return 'load_error'
  if (source && input.tables.some((table) => table.sourceId === source.id)) return 'ready'
  if (mode === 'builtin') return 'builtin_create_table'
  return source ? 'external_add_table' : 'external_config_connection'
}
