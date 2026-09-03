import type { ReactElement } from 'react'
import type { ApplicationLifecycle, WorkflowRunPayload } from '../../../../typings'
import { workflowDevelopmentContinuation } from '../../developmentContinuation'
import EntityDesignGateCard from './EntityDesignGateCard'

/** 实体确认后显示原任务尚缺的实体；完成的实体运行不影响原开发门禁的可操作性。 */
export default function RemainingEntityBindingsCard({
  workflow,
  lifecycle,
  disabled,
  onJump
}: {
  workflow: WorkflowRunPayload
  lifecycle?: ApplicationLifecycle
  disabled?: boolean
  onJump?: (entityId: string, workflow: WorkflowRunPayload) => void
}): ReactElement | null {
  const continuation = workflowDevelopmentContinuation(workflow)
  if (
    workflow.summary.phase !== 'entity_source_binding' ||
    workflow.summary.status !== 'completed' ||
    continuation?.status !== 'awaiting_entity_binding'
  )
    return null
  const source = lifecycle?.activeExecutions?.[continuation.sourceRunId]
  return (
    <EntityDesignGateCard
      disabled={disabled || (Boolean(lifecycle) && source?.status !== 'awaiting_user')}
      entities={continuation.remainingEntityIds.map((id) => ({ entity_id: id }))}
      explanation={`当前实体已确认，开发「${continuation.target.label}」还需完成以下实体。`}
      onJump={(entityId) => onJump?.(entityId, workflow)}
    />
  )
}
