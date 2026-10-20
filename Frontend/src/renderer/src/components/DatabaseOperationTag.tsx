import { Tag } from 'antd'
import type { ReactElement } from 'react'
import type { DatabaseTableOperation } from '../typings/managedDatabase'

const operationNames: Record<DatabaseTableOperation['mode'], string> = {
  CREATE: '创建', REBUILD: '重建', DROP: '删除', RELEASE: '资源回收'
}
const operationStates: Record<DatabaseTableOperation['status'], string> = {
  RUNNING: '执行中', SUCCEEDED: '成功', FAILED: '失败', UNKNOWN: '结果待核对'
}

/** 为实体结果和数据源记录返回相同的资源操作名称。 */
export function databaseOperationName(mode: DatabaseTableOperation['mode']): string {
  return operationNames[mode]
}

/** 展示最近资源操作的类型及状态，受理回执尚无类型时只展示状态。 */
export default function DatabaseOperationTag({ operation, status }: {
  operation?: DatabaseTableOperation | null
  status?: DatabaseTableOperation['status']
}): ReactElement | null {
  if (!status) return null
  return <Tag>最近操作：{operation ? `${databaseOperationName(operation.mode)} · ` : ''}{operationStates[status]}</Tag>
}
