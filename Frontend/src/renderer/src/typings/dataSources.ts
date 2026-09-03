/** 独立数据源目录中的数据库连接模式。 */
export type DatabaseSourceMode = 'builtin' | 'dbid' | 'direct'

/** 描述参数及 JSON 字段可选择的通用类型。 */
export type DataSourceFieldType = 'string' | 'integer' | 'number' | 'boolean' | 'object' | 'array' | 'null'

/** 描述一个外部 API Header。 */
export type DataSourceHeader = {
  name: string
  value: string
}

/** 描述外部 API 的路径或查询参数。 */
export type DataSourceParameter = {
  name: string
  type: DataSourceFieldType
  required: boolean
  description: string
}

/** 描述外部 API 操作模板。 */
export type DataSourceOperation = {
  id: string
  name: string
  method: 'GET' | 'POST' | 'PUT' | 'DELETE'
  path: string
  pathParameters: DataSourceParameter[]
  queryParameters: DataSourceParameter[]
  headers: DataSourceHeader[]
  requestSample?: unknown
  responseSample?: unknown
  requestFieldDescriptions: Record<string, string>
  responseFieldDescriptions: Record<string, string>
  requestFieldTypes: Record<string, DataSourceFieldType>
  responseFieldTypes: Record<string, DataSourceFieldType>
}

/** 描述外部 API 域名下的普通接口目录。 */
export type DataSourceDirectory = {
  id: string
  name: string
  operations: DataSourceOperation[]
}

/** 描述数据库数据源的脱敏公开信息。 */
export type DatabaseDataSource = {
  type: 'database'
  id: string
  name: string
  mode: DatabaseSourceMode
  domain?: string
  port?: number
  schema?: string
  userName?: string
  dbid?: string
  hasPassword: boolean
}

/** 描述外部 API 数据源的公开信息。 */
export type ExternalApiDataSource = {
  type: 'external_api'
  id: string
  name: string
  baseUrl: string
  baseUrlConfigKey?: string
  timeoutMs: number
  headers: DataSourceHeader[]
  directories: DataSourceDirectory[]
}

/** 独立数据源目录的前端状态。 */
export type DataSourceCatalog = {
  sources: Array<DatabaseDataSource | ExternalApiDataSource>
}

/** 创建或更新数据库数据源时提交的编辑值。 */
export type DatabaseDataSourceInput = Omit<DatabaseDataSource, 'id' | 'hasPassword'> & {
  id?: string
  passwordCiphertext?: string
}

/** 创建或更新外部 API 数据源时提交的编辑值。 */
export type ExternalApiDataSourceInput = Omit<ExternalApiDataSource, 'id'> & {
  id?: string
}

/** 独立数据源动作返回的校验信息。 */
export type DataSourceValidation = {
  valid: boolean
  connection: 'ok' | 'not_tested'
}
