import { runDataSourceAction } from './dataSources'
import type { DatabaseDataPage, DatabaseTableOperation } from '../typings/managedDatabase'

/** 分页读取当前内置表真实数据，不提交筛选条件或任意 SQL。 */
export async function requestDatabaseTablePage(workspaceRoot: string, sourceId: string, table: string,
  pageNumber: number, pageSize: number, signal?: AbortSignal): Promise<DatabaseDataPage> {
  const result = await runDataSourceAction(workspaceRoot, 'database_rows', { sourceId, table, pageNumber, pageSize }, '读取当前表调试数据。', signal)
  if (!result.dataPage) throw new Error('接口未返回表数据。')
  return result.dataPage
}

/** 读取明确有界的最近表操作，不能当作完整业务数据审计。 */
export async function requestDatabaseTableOperations(workspaceRoot: string, sourceId: string, table: string,
  signal?: AbortSignal): Promise<{ operations: DatabaseTableOperation[]; historyLimit: number }> {
  const result = await runDataSourceAction(workspaceRoot, 'database_operations', { sourceId, table }, '读取当前表最近操作。', signal)
  if (!result.operations || typeof result.historyLimit !== 'number') throw new Error('接口未返回操作记录。')
  return { operations: result.operations, historyLimit: result.historyLimit }
}
