import { Button } from 'antd'
import { useEffect, useState, type ReactElement } from 'react'
import { BuiltinAuthorizationTaskCard, useBuiltinAuthorizationActions, useBuiltinAuthorizationTask } from '../BuiltinAuthorization'
import { loadWorkspaceApplicationConfig } from '../../service/applicationStorage'
import { cx } from '../../utils'

/** 只在当前内置数据库页签读取正式权限配置，失败时允许重新读取。 */
export function BuiltinAuthorizationConfigGate({ workspaceRoot }: { workspaceRoot: string }): ReactElement | null {
  const [config, setConfig] = useState<{ workspace: string; enabled?: boolean; error?: boolean }>()
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    if (!workspaceRoot) return
    let disposed = false
    void loadWorkspaceApplicationConfig(workspaceRoot).then(
      (application) => {
        if (!disposed) setConfig({ workspace: workspaceRoot, enabled: application.authorization?.enabled === true })
      },
      () => {
        if (!disposed) setConfig({ workspace: workspaceRoot, error: true })
      }
    )
    return () => { disposed = true }
  }, [workspaceRoot, revision])

  const current = config?.workspace === workspaceRoot ? config : undefined
  if (current?.error) {
    return <section className="source-drawer-authorization" aria-label="权限初始化模块">
      <div className={`source-builtin-authorization ${cx('authorization-bootstrap-panel')}`}>
        <strong>权限初始化</strong>
        <p role="alert">未能读取应用权限配置。</p>
        <Button size="small" onClick={() => { setConfig(undefined); setRevision((value) => value + 1) }}>重新读取配置</Button>
      </div>
    </section>
  }
  return current?.enabled ? <section className="source-drawer-authorization" aria-label="权限初始化模块">
    <BuiltinAuthorizationSection key={workspaceRoot} workspaceRoot={workspaceRoot} enabled />
  </section> : null
}

/** 展示抽屉入口；调度回执只提供反馈，任务详情始终来自共享只读 Hook。 */
export default function BuiltinAuthorizationSection({ workspaceRoot, enabled, disabled = false }: {
  workspaceRoot: string
  enabled: boolean
  disabled?: boolean
}): ReactElement | null {
  const workspace = enabled && workspaceRoot ? workspaceRoot : undefined
  const { value, refreshing, error, refresh } = useBuiltinAuthorizationTask(workspace)
  const actions = useBuiltinAuthorizationActions(workspace, disabled)
  if (!workspace) return null
  if (value) {
    return <div className="source-builtin-authorization">
      <div className="source-builtin-authorization-details" role="region" aria-label="权限初始化详情" tabIndex={0}>
        {actions.error && <p role="alert">{actions.error}</p>}
        <BuiltinAuthorizationTaskCard task={value} workspace={workspace} disabled={disabled || refreshing || Boolean(error)} variant="compact" />
      </div>
      {error && <div className={cx('authorization-bootstrap-panel')}>
        <p role="alert">读取权限初始化状态失败。{error}</p>
        <Button size="small" loading={refreshing} onClick={refresh}>重新读取状态</Button>
      </div>}
    </div>
  }
  return <section className={`source-builtin-authorization ${cx('authorization-bootstrap-panel')}`}
    aria-label="权限初始化" aria-busy={actions.busy || refreshing || value === undefined && !error}>
    <strong>权限初始化</strong>
    {actions.error && <p role="alert">{actions.error}</p>}
    {error ? <p role="alert">读取权限初始化状态失败。{error}</p>
      : <p role="status">{actions.progress?.message || (refreshing || value === undefined ? '正在读取权限初始化状态…' : '尚未初始化')}</p>}
    {!error && value === null && !refreshing && <Button type="primary" size="small"
      loading={actions.busy} disabled={disabled || actions.busy} onClick={() => { void actions.execute('run_patch') }}>开始初始化</Button>}
    {(error || actions.error || (value === null && !actions.busy && !refreshing)) &&
      <Button size="small" className="source-builtin-read-state" loading={refreshing} onClick={refresh}>刷新状态</Button>}
  </section>
}
