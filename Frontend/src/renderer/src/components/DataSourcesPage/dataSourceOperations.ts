import type { DataSourceCatalog, DataSourceOperation, DataSourceParameter, ExternalApiDataSource } from '../../typings/dataSources'
import { JSON_FIELD_TYPES, PATH_FIELD_TYPES } from './jsonFieldTypes'

/** 从指定接口的详情响应中取得编辑目标，缺失时不使用列表摘要兜底。 */
export function requireOperationDetails(catalog: DataSourceCatalog, sourceId: string, operationId: string): DataSourceOperation {
  const source = catalog.sources.find((item): item is ExternalApiDataSource => item.type === 'external_api' && item.id === sourceId)
  const operation = source?.directories.flatMap((directory) => directory.operations).find((item) => item.id === operationId)
  if (!operation) throw new Error('目标接口不存在，请刷新后重试。')
  return operation
}

/** 校验分区后的参数与路径占位符，前端错误与后端规则保持一致。 */
export function validateOperationParameters(path: string, pathParameters: DataSourceParameter[], queryParameters: DataSourceParameter[]): void {
  if (!path.startsWith('/')) throw new Error('外部 API 操作路径必须以 / 开头。')
  const placeholders = new Set(Array.from(path.matchAll(/\{([^{}\/]+)\}/g), (match) => match[1]))
  if (/[{}]/.test(path.replace(/\{([^{}\/]+)\}/g, ''))) throw new Error('API 路径占位符格式无效。')
  const pathNames = new Set<string>()
  if (pathParameters.length + queryParameters.length > 50) throw new Error('Path 和 Query 参数合计不能超过 50 个。')
  for (const [location, parameters] of [['path', pathParameters], ['query', queryParameters]] as const) {
    const names = new Set<string>()
    for (const parameter of parameters) {
      const name = parameter.name.trim()
      if (!name) throw new Error('参数名称不能为空。')
      if (names.has(name.toLowerCase())) throw new Error('同一位置的参数名称不能重复。')
      names.add(name.toLowerCase())
      if (!JSON_FIELD_TYPES.includes(parameter.type)) throw new Error('参数字段类型无效。')
      if (location === 'path') {
        if (!parameter.required) throw new Error('Path 参数必须为必填。')
        if (!PATH_FIELD_TYPES.includes(parameter.type)) throw new Error('Path 参数只能使用基础类型。')
        pathNames.add(name)
      }
    }
  }
  if (placeholders.size !== pathNames.size || [...placeholders].some((name) => !pathNames.has(name))) {
    throw new Error('API 路径占位符必须与 Path 参数一一对应。')
  }
}

/** 在最新完整域名上应用目录引用和本次接口编辑，禁止摘要覆盖其他接口或共享配置。 */
export function mergeExternalSourceChanges(latest: ExternalApiDataSource, candidate: ExternalApiDataSource, editedOperation?: DataSourceOperation): ExternalApiDataSource {
  if (latest.id !== candidate.id) throw new Error('目标域名不一致，请刷新后重试。')
  const latestOperations = new Map(latest.directories.flatMap((directory) => directory.operations).map((operation) => [operation.id, operation]))
  return {
    ...latest,
    directories: candidate.directories.map((directory) => ({
      ...directory,
      operations: directory.operations.map((reference) => {
        if (reference.id === editedOperation?.id) return editedOperation
        const fullOperation = latestOperations.get(reference.id)
        if (!fullOperation) throw new Error('目录中的接口已不存在，请刷新后重试。')
        return fullOperation
      })
    }))
  }
}
