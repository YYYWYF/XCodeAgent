import { useEffect, useRef, type ReactElement } from 'react'
import { authorizationTaskWorkflowResult } from '../../service/builtinAuthorization'
import type { WorkflowAuthorizationBootstrap } from '../../service/authorizationBootstrap'
import BuiltinAuthorizationTaskCard from './BuiltinAuthorizationTaskCard'
import { useBuiltinAuthorizationTask } from './useBuiltinAuthorizationTask'

/** 只有当前 Run 获得工作区身份，历史卡保持初始快照。 */
export function BuiltinAuthorizationWorkflowPanel({ result, workspace, disabled, onRefreshed }: {
  result: WorkflowAuthorizationBootstrap
  workspace?: string
  disabled?: boolean
  onRefreshed?: (result: WorkflowAuthorizationBootstrap) => void
}): ReactElement {
  const { value: liveTask, refreshing } = useBuiltinAuthorizationTask(workspace)
  const refreshedCallback = useRef(onRefreshed)
  refreshedCallback.current = onRefreshed
  useEffect(() => {
    if (liveTask) refreshedCallback.current?.(authorizationTaskWorkflowResult(liveTask))
  }, [liveTask])
  const task = liveTask || {
    stage: result.initializationStage || (result.seedReady ? 'done' : result.schemaReady ? 'seed' : 'schema'),
    status: result.initializationStatus || (result.seedReady ? 'ready' : 'needs_attention'),
    schemaReady: Boolean(result.schemaReady), seedReady: Boolean(result.seedReady),
    message: result.message || '权限初始化结果待核对。', schemaOperationId: result.operationId,
    initializedTables: result.initializedTables, seedOperationId: result.seedOperationId,
    failureCategory: result.failureCategory, nextAction: result.nextAction === 'resume' || result.nextAction === 'retry' ? result.nextAction : undefined
  }
  return <BuiltinAuthorizationTaskCard task={task} workspace={workspace} disabled={disabled || Boolean(refreshing)} />
}
