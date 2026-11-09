import { CheckCircleOutlined, ClockCircleOutlined, WarningOutlined } from '@ant-design/icons'
import { Button, message } from 'antd'
import { useEffect, useRef, useState, type ReactElement } from 'react'
import { authorizationTaskWorkflowResult, runBuiltinPatch, type BuiltinPatchResult } from '../../../../service/builtinAuthorization'
import { cx } from '../../../../utils'
import './AuthorizationBootstrapPanel.less'
import { useBuiltinAuthorizationTask } from './useBuiltinAuthorizationTask'
import type { WorkflowAuthorizationBootstrap } from '../../../../service/authorizationBootstrap'

const steps = ['权限数据表初始化', '权限数据初始化', '初始化结果核验']

/** 工作流与调试入口共用一个三阶段投影，只在服务端明确允许时提供恢复。 */
export default function BuiltinAuthorizationTaskCard({ task, workspace, disabled = false, onChanged }: {
  task: BuiltinPatchResult
  workspace?: string
  disabled?: boolean
  onChanged?: () => void
}): ReactElement {
  const [busy, setBusy] = useState(false)
  const identity = useRef(workspace)
  identity.current = workspace
  const generation = useRef(0)
  useEffect(() => {
    generation.current += 1
    setBusy(false)
    return () => { generation.current += 1 }
  }, [workspace])
  const ready = task.schemaReady && task.seedReady
  const running = !ready && ['pending', 'running', 'waiting'].includes(task.status)
  const stage = task.stage === 'schema' ? 0 : task.stage === 'seed' ? 1 : 2
  const completedDescriptions = ['4 张表已创建并核验', ready ? '权限数据写入完成' : '权限数据结果待核验', '权限表与权限数据均已核验就绪']
  const runningDescriptions = ['正在自动检查权限表和原建表操作', '正在自动查询数据写入结果', '正在核验初始化结果']
  /** 手工动作仍经过统一 AG-UI，恢复不表示批准重新提交未知操作。 */
  async function act(action: 'resume' | 'refresh_patch'): Promise<void> {
    if (!workspace || busy) return
    const owner = generation.current
    setBusy(true)
    try {
      await runBuiltinPatch(workspace, action)
      if (identity.current === workspace && owner === generation.current) onChanged?.()
    } catch (error) {
      if (identity.current === workspace && owner === generation.current) message.error(error instanceof Error ? error.message : '查询失败')
    } finally {
      if (identity.current === workspace && owner === generation.current) setBusy(false)
    }
  }
  return <section className={cx('authorization-bootstrap-panel', 'builtin-authorization-task')} aria-label="权限数据库初始化" aria-busy={running}>
    <header><strong>权限数据库初始化</strong><span role="status">{ready ? '已完成' : running ? '运行中' : task.status === 'failed' ? '失败' : '待处理'}</span></header>
    <ol>{steps.map((label, index) => {
      const done = ready || index === 0 && task.schemaReady || index < stage && index > 0
      const current = !ready && index === stage
      let detail = index === 2 ? '等待权限数据写入完成' : '等待上一步完成'
      if (done) detail = completedDescriptions[index]
      else if (current) detail = running ? runningDescriptions[index] : task.message
      return <li key={label} aria-current={current ? 'step' : undefined}>
        <span className={cx(done ? 'builtin-task-success' : current && !running ? 'builtin-task-warning' : 'builtin-task-neutral')}>
          {done ? <CheckCircleOutlined /> : current ? running ? <ClockCircleOutlined /> : <WarningOutlined /> : <span className={cx('builtin-task-dot')} />}
        </span>
        <div><strong>{label}</strong><p>{detail}</p>{current && running && <div className={cx('builtin-task-progress')} role="progressbar" aria-label="自动初始化进行中" />}</div>
      </li>
    })}</ol>
    {running && <p>正在自动处理，无需手动刷新。</p>}
    {!running && !ready && <>
      {task.schemaOperationId && <p>建表操作 ID　{task.schemaOperationId}</p>}
      {task.seedOperationId && <p>数据操作 ID　{task.seedOperationId}</p>}
      <details><summary>查看详情</summary><p>{task.message}</p>{task.failureCategory && <code>{task.failureCategory}</code>}</details>
      {workspace && <div className={cx('builtin-task-actions')}>
        {task.nextAction === 'resume' && <Button loading={busy} disabled={disabled} onClick={() => { void act('resume') }}>重试</Button>}
        <Button loading={busy} disabled={disabled} onClick={() => { void act('refresh_patch') }}>查询原操作</Button>
      </div>}
    </>}
    {task.schemaReady && task.initializedTables?.length === 4 && <details><summary>以下四张权限表已初始化完成</summary>
      <ul>{task.initializedTables.map((name) => <li key={name}><code>{name}</code></li>)}</ul></details>}
    {ready && typeof task.resourceCount === 'number' && typeof task.roleGrantCount === 'number' && <p>资源点 {task.resourceCount} 条，角色授权 {task.roleGrantCount} 条</p>}
    {ready && typeof task.createdCount === 'number' && typeof task.existingCount === 'number' && <p>新增 {task.createdCount} 条，复用 {task.existingCount} 条</p>}
  </section>
}

/** 只有当前 Run 获得工作区身份，历史卡保持初始快照。 */
export function BuiltinAuthorizationWorkflowPanel({ result, workspace, disabled, onRefreshed }: {
  result: WorkflowAuthorizationBootstrap
  workspace?: string
  disabled?: boolean
  onRefreshed?: (result: WorkflowAuthorizationBootstrap) => void
}): ReactElement {
  const [revision, setRevision] = useState(0)
  const liveTask = useBuiltinAuthorizationTask(workspace, revision)
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
    failureCategory: result.failureCategory, nextAction: result.nextAction === 'resume' ? 'resume' as const : undefined
  }
  return <BuiltinAuthorizationTaskCard task={task} workspace={workspace} disabled={disabled}
    onChanged={() => setRevision((value) => value + 1)} />
}
