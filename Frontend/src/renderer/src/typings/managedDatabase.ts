/** 内置数据库单页预览，单元格文本保留 bigint/decimal 精度及 SQL NULL。 */
export type DatabaseDataPage = {
  sourceId: string
  status: 'SUCCEEDED'
  datasourceKey: string
  tableName: string
  columns: Array<{ name: string; type: string; nullable: boolean; primaryKey: boolean; autoIncrement: boolean; defaultValue: string | null; description: string }>
  rows: Array<Record<string, string | null>>
  pageNumber: number
  pageSize: number
  hasMore: boolean
  truncated: boolean
  columnFormats: Record<string, 'TEXT' | 'HEX'>
  filteringSupported: false
}

/** 当前表最近资源操作摘要，编号使用文本，不含 SQL 或管理账号。 */
export type DatabaseTableOperation = {
  operationId: string
  mode: 'CREATE' | 'REBUILD' | 'DROP' | 'RELEASE'
  tableName: string
  tableNames: string[]
  status: 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'UNKNOWN'
  createdAt: string
  executionResult: { errorCode: string | null; errorSummary: string | null; recoveryState: string }
}
