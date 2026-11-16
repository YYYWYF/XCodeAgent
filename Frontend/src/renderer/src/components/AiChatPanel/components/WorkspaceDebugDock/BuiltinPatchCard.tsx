import { Button } from 'antd'
import type { ReactElement } from 'react'
import type { BuiltinPatchResult, BuiltinPatchStage } from '../../../../service/builtinAuthorization'
import { BuiltinAuthorizationTaskCard, useBuiltinAuthorizationActions, useBuiltinAuthorizationTask } from '../../../BuiltinAuthorization'
import { cx } from '../../../../utils'
import './WorkspaceDebugDock.less'

type PatchCardState = {
  workspace?: string
  result?: BuiltinPatchResult
  progress?: { stage: BuiltinPatchStage; message: string }
  error?: string
  refreshing?: boolean
  refreshOriginal?: () => void
}

/** 调试面板订阅权威任务，调度期间保留原进度和异常提示。 */
export function useBuiltinPatchCard(workspace: string, enabled: boolean) {
  const scope = enabled && workspace ? workspace : undefined
  const live = useBuiltinAuthorizationTask(scope)
  const { progress, error, execute } = useBuiltinAuthorizationActions(scope)
  const card = live.value || progress || error || (live.value === null && live.refreshing)
    ? { result: live.value || undefined, workspace: scope, progress, error, refreshing: live.refreshing, refreshOriginal: () => { void execute('refresh_patch') } }
    : undefined
  return { card, execute }
}

/** 调试入口复用统一权限卡片和只读轮询，无独立 Schema/Seed 通道。 */
export default function BuiltinPatchCard({ card, disabled }: {
  card: PatchCardState
  disabled: boolean
}): ReactElement {
  // 调试区位于固定底栏，共用任务卡必须放回受限高度的滚动容器，避免撑出窗口。
  return <div className={cx('builtin-patch-card')} role="region" aria-label="权限初始化结果" tabIndex={0}>
    {card.progress && <div role="status">{card.progress.message}</div>}
    {card.error && <div className={cx('authorization-bootstrap-panel')}>
      <p role="alert">{card.error}</p>
      {!card.result && <Button disabled={disabled} onClick={card.refreshOriginal}>刷新状态</Button>}
    </div>}
    {card.result
      ? <BuiltinAuthorizationTaskCard task={card.result} workspace={card.workspace} disabled={disabled || Boolean(card.progress) || Boolean(card.refreshing)} />
      : !card.progress && !card.error && <div role="status">正在调度权限初始化…</div>}
  </div>
}
