import { useEffect, useRef, useState, type ReactElement } from 'react'
import {
  BuiltinPatchResponseError,
  runBuiltinPatch,
  type BuiltinPatchResult,
  type BuiltinPatchStage
} from '../../../../service/builtinAuthorization'
import BuiltinAuthorizationTaskCard from '../ProcessSteps/BuiltinAuthorizationTaskCard'
import { useBuiltinAuthorizationTask } from '../ProcessSteps/useBuiltinAuthorizationTask'
import './WorkspaceDebugDock.less'

type PatchCardState = {
  workspace?: string
  onChanged?: () => void
  result?: BuiltinPatchResult
  progress?: { stage: BuiltinPatchStage; message: string }
}

/** 读取当前应用的补丁结果，隔离切换工作区后的迟到事件。 */
export function useBuiltinPatchCard(workspace: string, enabled: boolean) {
  const scope = enabled ? workspace : ''
  const [revision, setRevision] = useState(0)
  const live = useBuiltinAuthorizationTask(scope || undefined, revision)
  const [card, setCard] = useState<{ workspace: string; value: PatchCardState }>()
  const active = useRef<{ workspace: string }>()
  const currentScope = useRef(scope)
  currentScope.current = scope

  useEffect(() => {
    active.current = undefined
    return () => { active.current = undefined }
  }, [scope])

  /** 点击即反馈，网络断开时只提供原操作查询。 */
  async function execute(action: 'run_patch' | 'refresh_patch'): Promise<void> {
    if (!scope || active.current?.workspace === scope) return
    const owner = { workspace: scope }
    const previous = card?.workspace === scope ? card.value.result : undefined
    const request: { stage: BuiltinPatchStage } = { stage: action === 'run_patch' ? 'schema' : 'query' }
    active.current = owner
    setCard({ workspace: scope, value: { result: previous, progress: {
      stage: request.stage, message: action === 'run_patch' ? '正在检查权限表…' : '正在查询原操作…'
    } } })
    /** 仅向仍归属本次请求的工作区投递结果。 */
    function update(value: PatchCardState): void {
      if (currentScope.current === scope && active.current === owner) setCard({ workspace: scope, value })
    }
    try {
      const result = await runBuiltinPatch(scope, action, (nextStage, message) => {
        request.stage = nextStage
        update({ result: previous, progress: { stage: nextStage, message } })
      })
      if (result) {
        update({ result })
        if (currentScope.current === scope && active.current === owner) setRevision((value) => value + 1)
      }
    } catch (error) {
      const rejected = error instanceof BuiltinPatchResponseError
      const { stage } = request
      update({ result: {
        ...previous,
        stage: stage === 'query' ? previous?.stage || 'schema' : stage,
        schemaReady: stage === 'seed' || stage === 'verify' || Boolean(previous?.schemaReady),
        status: rejected ? 'failed' : 'needs_attention',
        seedReady: false,
        message: rejected ? error.message : '连接中断，无法确认本次操作结果。请查询原操作。',
        nextAction: undefined
      } })
    } finally {
      if (active.current === owner) active.current = undefined
    }
  }

  const current = card?.workspace === scope ? card.value : undefined
  return { card: current || live ? { ...current, result: live || current?.result,
    workspace: scope, onChanged: () => setRevision((value) => value + 1) } : undefined, execute }
}

/** 调试入口复用统一权限卡片和只读轮询，无独立 Schema/Seed 通道。 */
export default function BuiltinPatchCard({ card, disabled }: {
  card: PatchCardState
  disabled: boolean
}): ReactElement {
  return card.result
    ? <BuiltinAuthorizationTaskCard task={card.result} workspace={card.workspace} disabled={disabled} onChanged={card.onChanged} />
    : <div role="status">{card.progress?.message || '正在调度权限初始化…'}</div>
}
