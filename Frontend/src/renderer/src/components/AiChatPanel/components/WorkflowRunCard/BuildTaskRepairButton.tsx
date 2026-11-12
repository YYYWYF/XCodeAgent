import { useContext, useEffect, useRef, useState } from 'react'
import { Alert, Button, Space, Typography } from 'antd'
import { AppstoreOutlined, DownOutlined, ExclamationCircleOutlined, FileTextOutlined, LoadingOutlined, ToolOutlined } from '@ant-design/icons'
import type { WorkflowBuildExecutionTask } from '../../../../typings'
import type { BuildTaskRepairDraft, RepairTaskView, TaskRepairAction } from '../../../../service/buildTaskRepair'
import { BuildTaskRetryContext } from './BuildTaskRetryButton'
import { BuildTaskFileList } from './BuildTaskFileList'
import { canRepairBuildTask } from './buildTaskRepairEligibility'
import { cx } from '../../../../utils'
import './BuildTaskRepairButton.less'

/** 点击后直接根据报错生成草稿，在原任务卡内明确确认后才进入执行。 */
export default function BuildTaskRepairButton({ task }: { task: WorkflowBuildExecutionTask }): JSX.Element | null {
  const { repair, sourceRunId, pending: sharedPending } = useContext(BuildTaskRetryContext)
  const [pending, setPending] = useState<TaskRepairAction | null>(null)
  const [draft, setDraft] = useState<BuildTaskRepairDraft | null>(null)
  const [error, setError] = useState('')
  const lock = useRef(false)
  const currentRun = useRef(sourceRunId)
  currentRun.current = sourceRunId
  /** 原卡片切换到新的 Build Run 后不能继续展示或确认来源 Run 的草稿。 */
  useEffect(() => {
    setDraft(null)
    setError('')
  }, [sourceRunId])
  const identity = String(task.id || task.task_id || '')
  if (!repair || !canRepairBuildTask(task)) return null

  /** 自动诊断只生成待确认草稿；确认动作独立提交，失败原因保留在原卡片。 */
  async function act(action: TaskRepairAction): Promise<void> {
    if (lock.current || sharedPending || !repair) return
    lock.current = true
    const submittedRun = sourceRunId
    setPending(action)
    setError('')
    try {
      const result = await repair(identity, action, undefined,
        ['confirm', 'discard', 'regenerate'].includes(action) ? draft?.draftDigest : undefined)
      if (currentRun.current !== submittedRun) return
      if ((action === 'generate' || action === 'regenerate') && !result)
        throw new Error('未收到修复方案，请重新修复任务。')
      setDraft(result)
    } catch (reason) {
      if (currentRun.current !== submittedRun) return
      setError(reason instanceof Error ? reason.message : '任务修复失败。')
      // 重新生成先消费旧稿，失败后不能继续确认旧版本。
      if (action === 'regenerate') setDraft(null)
    } finally {
      lock.current = false
      setPending(null)
    }
  }

  return (
    <>
      <span className={cx('workflow-task-repair-action')}>
        <Button size="small" icon={<ToolOutlined />} loading={pending === 'generate'}
          disabled={sharedPending || Boolean(pending) || Boolean(draft)}
          onClick={(event) => { event.stopPropagation(); void act('generate') }}>
          {pending === 'generate' ? '正在修复' : draft ? '修复方案待确认' : '修复任务'}
        </Button>
      </span>
      {pending === 'generate' && <div className={cx('workflow-task-repair-progress')} role="status">
        <LoadingOutlined aria-hidden="true" />
        <span>正在分析报错并生成修复方案…</span>
      </div>}
      {!draft && error && <div className={cx('workflow-task-repair-error')} role="alert">
        修复失败：{error}
      </div>}
      {draft && <section className={cx('workflow-task-repair-review')} aria-label="任务修复方案确认" aria-busy={Boolean(pending)}>
          <div className={cx('workflow-task-repair-heading')}>
            <span className={cx('workflow-task-repair-heading-icon')}><ToolOutlined /></span>
            <Typography.Text strong>修复方案 · 等待确认</Typography.Text>
          </div>
          <div className={cx('workflow-task-repair-info', 'workflow-task-repair-cause')}>
            <div className={cx('workflow-task-repair-info-label')}><ExclamationCircleOutlined />失败根因</div>
            <RepairExpandableText text={draft.reason} label="原因" />
          </div>
          <RepairChanges before={draft.before} after={draft.after} />
          {error && <Alert type="error" showIcon message={error} />}
          <Space wrap className={cx('workflow-task-repair-controls')}>
            <Button disabled={Boolean(pending) || sharedPending} onClick={() => void act('discard')}>放弃修复</Button>
            <Button loading={pending === 'regenerate'} disabled={Boolean(pending) || sharedPending}
              onClick={() => void act('regenerate')}>重新生成</Button>
            <Button type="primary" loading={pending === 'confirm'} disabled={Boolean(pending) || sharedPending}
              onClick={() => void act('confirm')}>确认并执行</Button>
          </Space>
      </section>}
    </>
  )
}

/** 默认只展示与原计划不同的文件和增量说明，避免完整任务淹没实际修复点。 */
function RepairChanges({ before, after }: { before: RepairTaskView; after: RepairTaskView }): JSX.Element {
  const originals = new Map(before.files.map((file) => [file.path, file] as const))
  const changed = after.files.filter((file) => {
    const original = originals.get(file.path)
    return !original || original.operation !== file.operation || original.description !== file.description
  })
  const instructions = after.description.startsWith(before.description)
    ? after.description.slice(before.description.length).trim().replace(/^修复补充：/, '')
    : after.description
  const groups = [
    { title: '计划外文件新增', files: changed.filter((file) => !originals.has(file.path)) },
    { title: '计划内文件调整', files: changed.filter((file) => originals.has(file.path)) }
  ]
  return <div className={cx('workflow-task-repair-view')}>
    <div className={cx('workflow-task-repair-info', 'workflow-task-repair-adjustment')}>
      <div className={cx('workflow-task-repair-info-label')}><AppstoreOutlined />本次调整</div>
      {instructions && <RepairExpandableText text={instructions} label="说明" />}
    </div>
    {groups.filter((group) => group.files.length > 0).map((group) => <details key={group.title} className={cx('workflow-task-repair-files')}>
      <summary><span><FileTextOutlined /> {group.title} <span className={cx('workflow-task-repair-count')}>{group.files.length}</span></span>
        <DownOutlined aria-hidden="true" /></summary>
      <div className={cx('workflow-task-repair-file-list')}>
        <BuildTaskFileList files={group.files.map((file) => {
          const original = originals.get(file.path)
          const description = original && file.description.startsWith(original.description)
            ? file.description.slice(original.description.length).trim().replace(/^修复补充：/, '')
            : file.description
          return {
            path: file.path, name: file.path.split('/').pop() || file.path,
            operations: [file.operation], descriptions: description ? [description] : []
          }
        })} />
      </div>
    </details>)}
  </div>
}

/** 长文本默认展示两行，并保留可重复展开和收起的操作。 */
function RepairExpandableText({ text, label }: { text: string; label: string }): JSX.Element {
  const [expanded, setExpanded] = useState(false)
  const [truncated, setTruncated] = useState(false)
  return <div className={cx('workflow-task-repair-text')}>
    <Typography.Paragraph ellipsis={expanded ? false : { rows: 2, onEllipsis: setTruncated }}>
      {text}
    </Typography.Paragraph>
    {(expanded || truncated) && <button type="button" className={cx('workflow-task-repair-text-toggle')}
      aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
      {expanded ? `收起${label}` : `展开${label}`}
    </button>}
  </div>
}
