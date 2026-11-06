import { randomUUID } from '@ag-ui/client'
import { Alert, Button, Checkbox, Collapse, Modal, Space, Typography } from 'antd'
import { useEffect, useRef, useState, type ReactElement } from 'react'
import {
  runAgentUiDesignTransfer,
  type AgentUiTransferInput,
  type AgentUiTransferPayload,
  type AgentUiTransferPlan
} from '../../service/agentUiDesignTransfer'
import {
  runAgentUiDependencies,
  type AgentUiDependencyInput,
  type AgentUiDependencyPayload,
  type AgentUiDependencyPlan
} from '../../service/agentUiDependencies'
import { cx } from '../../utils'
import { transferMessages } from './messages'
import './index.less'

type Props = { workspaceRoot: string; disabled?: boolean }
type Action = AgentUiTransferInput['action'] | 'dependency-plan' | 'dependency-confirm'

/** 展示当前工程逐文件审阅与明确确认；跨工作区/断流不重提写入。 */
export default function AgentUiTransfer({ workspaceRoot, disabled }: Props): ReactElement {
  const t = transferMessages()
  const [open, setOpen] = useState(false)
  const [plan, setPlan] = useState<AgentUiTransferPlan>()
  const [dependency, setDependency] = useState<AgentUiDependencyPlan>()
  const [result, setResult] = useState<AgentUiTransferPayload | AgentUiDependencyPayload>()
  const [busy, setBusy] = useState(false)
  const [reviewed, setReviewed] = useState(false)
  const [error, setError] = useState('')
  const thread = useRef(randomUUID())
  const controller = useRef<AbortController>()
  const mounted = useRef(true)
  const [pending, setPending] = useState<{ kind: 'transfer' | 'dependency'; id: string }>()

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      controller.current?.abort()
    }
  }, [])

  /** 对当前请求投影真实进度；清理后的旧回执不能覆盖新工作区。 */
  const accept = (value: AgentUiTransferPayload | AgentUiDependencyPayload): void => {
    if (mounted.current) setResult(value)
  }

  /** 只显式执行当前审阅身份，查询和停止始终绑定原任务。 */
  const run = async (action: Action): Promise<void> => {
    if (busy && action !== 'cancel') return
    // 先断开原运行，即使后端还在复算、尚未落盘任务记录，也能取消写入前的准备。
    if (action === 'cancel') controller.current?.abort()
    if (action !== 'cancel') {
      setBusy(true)
      controller.current = new AbortController()
    }
    const signal = action === 'cancel' ? undefined : controller.current?.signal
    setError('')
    if (action !== 'cancel') setReviewed(false)
    try {
      let value: AgentUiTransferPayload | AgentUiDependencyPayload
      if (
        action.startsWith('dependency-') ||
        (['get', 'cancel'].includes(action) && pending?.kind === 'dependency')
      ) {
        const input: AgentUiDependencyInput = {
          workspace: workspaceRoot,
          action:
            action === 'dependency-plan'
              ? 'plan'
              : action === 'dependency-confirm'
                ? 'confirm'
                : (action as 'get' | 'cancel'),
          source: 'local-vendor',
          ...(dependency && action !== 'dependency-plan'
            ? { planId: dependency.planId, reviewId: dependency.reviewId }
            : {}),
          ...(['get', 'cancel'].includes(action) ? { planId: pending?.id } : {})
        }
        if (action === 'dependency-confirm' && dependency)
          setPending({ kind: 'dependency', id: dependency.planId })
        value = await runAgentUiDependencies(input, {
          threadId: thread.current,
          signal,
          onUpdate: accept
        })
        if (!mounted.current) return
        if (value.plan) {
          setDependency(value.plan)
          setPlan(undefined)
        }
        if (value.installation?.state === 'verified') {
          setDependency(undefined)
          setPlan(undefined)
        }
      } else {
        const input: AgentUiTransferInput = {
          workspace: workspaceRoot,
          action: action as AgentUiTransferInput['action'],
          ...(plan && action !== 'plan'
            ? {
                installationPlanId: plan.installation.planId,
                targetSourceRoot: plan.target.sourceRoot,
                planId: plan.planId
              }
            : {}),
          ...(['get', 'cancel'].includes(action) ? { planId: pending?.id } : {})
        }
        if (['initialize', 'confirm'].includes(action) && plan)
          setPending({ kind: 'transfer', id: plan.planId })
        value = await runAgentUiDesignTransfer(input, {
          threadId: thread.current,
          signal,
          onUpdate: accept
        })
        if (!mounted.current) return
        if (value.plan) {
          setPlan(value.plan)
          setDependency(undefined)
        }
        if (value.transfer) setPlan(undefined)
      }
      accept(value)
      if (value.status === 'failed') setError(value.error?.message || t.error)
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : t.unknown)
    } finally {
      if (mounted.current && action !== 'cancel') setBusy(false)
    }
  }

  const transfer = result && 'transfer' in result ? result.transfer : undefined
  const installation = result && 'installation' in result ? result.installation : undefined
  const record = transfer || installation
  const canExecute = reviewed && !busy && (dependency || plan?.status === 'reviewable')
  const changes =
    plan?.changes.map((item) => ({
      path: item.targetPath,
      operation: item.operation,
      diff: item.diff,
      after: item.after
    })) ||
    dependency?.changes.map((item) => ({
      path: item.path,
      operation: item.beforeSha256 ? 'modify' : 'add',
      diff: '',
      after: item.after
    })) ||
    []

  return (
    <>
      <Button disabled={disabled || !workspaceRoot} onClick={() => setOpen(true)}>
        {t.open}
      </Button>
      <Modal
        open={open}
        title={t.title}
        footer={null}
        width={900}
        onCancel={() => setOpen(false)}
        wrapClassName={cx('agent-ui-transfer-modal')}
      >
        <div className={cx('agent-ui-transfer')}>
          <Typography.Paragraph>{t.intro}</Typography.Paragraph>
          <Space wrap>
            <Button disabled={busy} onClick={() => void run('plan')}>
              {t.inspect}
            </Button>
            <Button disabled={busy} onClick={() => void run('dependency-plan')}>
              {t.prepare}
            </Button>
            {pending && (
              <Button disabled={busy} onClick={() => void run('get')}>
                {t.query}
              </Button>
            )}
            {busy && pending && (
              <Button danger onClick={() => void run('cancel')}>
                {t.stop}
              </Button>
            )}
          </Space>
          {error && <Alert type="error" showIcon message={error} />}
          {busy && (
            <div role="status">
              {t.pending} {result?.progress?.message}
            </div>
          )}
          {record && (
            <Alert
              type={
                ['failed', 'unknown', 'stale', 'stopped'].includes(record.state)
                  ? 'warning'
                  : 'info'
              }
              showIcon
              message={`${t.status}: ${record.state}`}
              description={
                <>
                  <p>{record.message}</p>
                  <p>{record.recovery}</p>
                  {transfer?.recordPath && (
                    <Typography.Text code>{transfer.recordPath}</Typography.Text>
                  )}
                </>
              }
            />
          )}
          {!plan && !dependency && !record && (
            <Typography.Paragraph type="secondary">{t.empty}</Typography.Paragraph>
          )}
          {plan && (
            <>
              <Alert
                type={plan.status === 'blocked' ? 'warning' : 'info'}
                showIcon
                message={
                  plan.status === 'blocked'
                    ? t.conflict
                    : plan.initialization.required
                      ? t.newTarget
                      : t.ready
                }
              />
              <Alert type="warning" showIcon message={plan.hostIntegration.reason} />
              <Typography.Text type="secondary">
                {t.source}: {plan.source.revision.slice(0, 12)} → {t.target}:{' '}
                {plan.target.sourceRoot}
              </Typography.Text>
              {plan.blockers.map((item) => (
                <Alert key={item} type="warning" message={item} />
              ))}
            </>
          )}
          {dependency && <Alert type="info" message={t.dependency} description={t.installHint} />}
          {changes.length > 0 && (
            <Collapse>
              {changes.map((item) => (
                <Collapse.Panel
                  key={item.path}
                  header={`${item.path} · ${
                    item.operation === 'conflict'
                      ? t.blocked
                      : item.operation === 'unchanged'
                        ? t.unchanged
                        : item.operation === 'add'
                          ? t.add
                          : t.modify
                  }`}
                >
                  <pre tabIndex={0}>{item.diff || item.after}</pre>
                </Collapse.Panel>
              ))}
            </Collapse>
          )}
          {(plan || dependency) && (
            <>
              <Collapse>
                {plan && (
                  <Collapse.Panel header={t.excluded} key="excluded">
                    {plan.excluded.map((item) => (
                      <p key={item.path}>
                        {item.path} — {item.reason}
                      </p>
                    ))}
                  </Collapse.Panel>
                )}
                <Collapse.Panel header={t.validation} key="validation">
                  {plan?.validation.map((item) => (
                    <p key={item}>{item}</p>
                  ))}
                  {dependency && <p>{dependency.checks.join(' / ')}</p>}
                  <p>{(plan || dependency)?.recovery}</p>
                </Collapse.Panel>
              </Collapse>
              <Checkbox
                checked={reviewed}
                disabled={busy}
                onChange={(event) => setReviewed(event.target.checked)}
              >
                {t.review}
              </Checkbox>
              <Button
                type="primary"
                disabled={!canExecute}
                loading={busy}
                onClick={() =>
                  void run(
                    dependency
                      ? 'dependency-confirm'
                      : plan?.initialization.required
                        ? 'initialize'
                        : 'confirm'
                  )
                }
              >
                {dependency ? t.install : plan?.initialization.required ? t.initialize : t.transfer}
              </Button>
            </>
          )}
        </div>
      </Modal>
    </>
  )
}
