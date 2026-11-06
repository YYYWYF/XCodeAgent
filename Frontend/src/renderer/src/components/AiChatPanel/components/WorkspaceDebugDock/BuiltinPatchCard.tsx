import { Button } from 'antd'
import { useEffect, useRef, useState, type ReactElement } from 'react'
import {
  BuiltinPatchResponseError,
  runBuiltinPatch,
  type BuiltinPatchResult,
  type BuiltinPatchStage
} from '../../../../service/builtinAuthorization'
import { cx } from '../../../../utils'
import './WorkspaceDebugDock.less'

type PatchCardState = {
  result?: BuiltinPatchResult
  progress?: { stage: BuiltinPatchStage; message: string }
}

/** 读取当前应用的补丁结果，隔离切换工作区后的迟到事件。 */
export function useBuiltinPatchCard(workspace: string, enabled: boolean) {
  const scope = enabled ? workspace : ''
  const [card, setCard] = useState<{ workspace: string; value: PatchCardState }>()
  const generation = useRef(0)
  const active = useRef<{ workspace: string }>()
  const currentScope = useRef(scope)
  currentScope.current = scope

  useEffect(() => {
    if (!scope) return
    const id = generation.current
    void runBuiltinPatch(scope, 'get').then((result) => {
      if (result && currentScope.current === scope && id === generation.current && active.current?.workspace !== scope) {
        setCard({ workspace: scope, value: { result } })
      }
    }).catch(() => undefined)
    return () => { generation.current += 1 }
  }, [scope])

  /** 点击即反馈，网络断开时只提供原操作查询。 */
  async function execute(action: 'run_patch' | 'refresh_patch'): Promise<void> {
    if (!scope || active.current?.workspace === scope) return
    generation.current += 1
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
      if (result) update({ result })
    } catch (error) {
      const rejected = error instanceof BuiltinPatchResponseError
      const { stage } = request
      update({ result: {
        ...previous,
        stage: stage === 'query' ? previous?.stage || 'schema' : stage,
        schemaReady: stage === 'write' || stage === 'verify' || Boolean(previous?.schemaReady),
        status: rejected ? 'failed' : 'unknown',
        message: rejected ? error.message : '连接中断，无法确认本次操作结果。请查询原操作。',
        nextAction: rejected && action !== 'refresh_patch' ? undefined : 'refresh'
      } })
    } finally {
      if (active.current === owner) active.current = undefined
    }
  }

  return { card: card?.workspace === scope ? card.value : undefined, execute }
}

const steps = [
  { id: 'schema', label: '检查权限表' },
  { id: 'write', label: '写入权限数据' },
  { id: 'verify', label: '回读核验' }
] as const

const statusLabels = {
  waiting: '等待执行',
  ready: '已核验',
  continue: '待继续',
  failed: '未完成',
  unknown: '结果待核对'
}

/** 在调试入口下显示权限补丁进度和结果，不占用工作流步骤。 */
export default function BuiltinPatchCard({ card, disabled, onAction }: {
  card: PatchCardState
  disabled: boolean
  onAction: (action: 'run_patch' | 'refresh_patch') => void
}): ReactElement {
  const { result, progress } = card
  const stage = progress?.stage === 'query'
    ? result?.seedOperationId ? 'verify' : result?.stage || 'schema'
    : progress?.stage || result?.stage
  const schemaReady = stage !== 'schema' && (result?.schemaReady || Boolean(progress && stage === 'write'))
  const ready = !progress && result?.status === 'ready'
  return (
    <section className={cx('builtin-patch-card')} aria-label="权限初始化补丁" aria-busy={Boolean(progress)}>
      <div className={cx('builtin-patch-heading')}>
        <strong>权限初始化补丁</strong>
        <span>{progress ? '处理中' : result && statusLabels[result.status]}</span>
      </div>
      <ol className={cx('builtin-patch-steps')}>
        {steps.map((step) => {
          const done = ready || step.id === 'schema' && schemaReady
          return <li key={step.id} aria-current={stage === step.id && !ready ? 'step' : undefined}>
            {step.label}{done ? ' · 已核验' : stage === step.id ? ' · 当前阶段' : ''}
          </li>
        })}
      </ol>
      <div role="status" aria-live="polite">{progress?.message || result?.message}</div>
      {(result?.schemaOperationId || result?.seedOperationId) && (
        <div className={cx('builtin-patch-details')}>
          {result.schemaOperationId && <span>建表操作 <code>{result.schemaOperationId}</code></span>}
          {result.seedOperationId && <span>数据操作 <code>{result.seedOperationId}</code></span>}
        </div>
      )}
      {ready && result && typeof result.createdCount === 'number' && typeof result.existingCount === 'number' && (
        <div className={cx('builtin-patch-details')}>
          <span>新增 {result.createdCount} 条，复用 {result.existingCount} 条</span>
          <span>资源点 {result.resourceCount} 条，角色授权 {result.roleGrantCount} 条</span>
        </div>
      )}
      {!progress && result?.nextAction && (
        <Button size="small" disabled={disabled} onClick={() => onAction(result.nextAction === 'run' ? 'run_patch' : 'refresh_patch')}>
          {result.nextAction === 'run' ? result.status === 'continue' ? '继续写入权限数据' : '重试写入检查'
            : result.status === 'unknown' ? '查询原操作' : '刷新执行状态'}
        </Button>
      )}
    </section>
  )
}
