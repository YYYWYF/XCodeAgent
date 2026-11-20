import {
  Alert,
  Button,
  Checkbox,
  Collapse,
  Empty,
  Modal,
  Select,
  Space,
  Spin,
  Typography
} from 'antd'
import { useEffect, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import { runAgentUiAcceptance } from '../../../service/agentUiAcceptance'
import type {
  AcceptanceCase,
  AcceptanceMatrix,
  AcceptanceOutcome,
  AcceptanceSource
} from '../../../service/agentUiAcceptance'
import { cx } from '../../../utils'
import './AgentUiAcceptanceReview.less'

const STATUS = {
  passed: '已核验通过',
  failed: '未通过',
  unknown: '结果未知',
  pending: '待核验',
  stale: '证据已失效',
  not_applicable: '不适用'
}
const LAYERS = {
  code: '代码检查',
  visual: '视觉场景',
  connection: '真实连接',
  business: '业务结果'
}
const SOURCES = {
  code: '目标代码检查',
  real: '真实运行',
  'official-mock': '官方 Mock',
  replay: '脱敏回放'
}

/** 单项事实由用户逐项核对；不预选通过，不把登记动作误写为执行测试。 */
function CaseReview({
  item,
  disabled,
  onSave
}: {
  item: AcceptanceCase
  disabled: boolean
  onSave: (source: AcceptanceSource, outcome: AcceptanceOutcome, checks: string[]) => Promise<void>
}): ReactElement {
  const [checks, setChecks] = useState<string[]>([])
  const [source, setSource] = useState<AcceptanceSource>(item.layer === 'code' ? 'code' : 'real')
  const [outcome, setOutcome] = useState<AcceptanceOutcome>('unknown')
  return (
    <Space direction="vertical" className={cx('agent-ui-acceptance-case')}>
      <Typography.Text type="secondary">
        {item.evidence
          ? `用户核验 · ${new Date(item.evidence.checkedAt).toLocaleString()} · ${SOURCES[item.evidence.source]}`
          : '尚无用户核验记录'}
      </Typography.Text>
      {item.applicable ? (
        <>
          {item.checks.map((check) => (
            <Checkbox
              key={check.id}
              disabled={disabled}
              checked={checks.includes(check.id)}
              onChange={(event) =>
                setChecks((previous) =>
                  event.target.checked
                    ? [...previous, check.id]
                    : previous.filter((key) => key !== check.id)
                )
              }
            >
              {check.label}
            </Checkbox>
          ))}
          <Space wrap>
            <Select
              aria-label="核验来源"
              disabled={disabled || item.layer !== 'visual'}
              value={source}
              onChange={setSource}
              options={
                item.layer === 'code'
                  ? [{ value: 'code', label: '目标代码检查' }]
                  : item.layer === 'visual'
                    ? [
                        { value: 'real', label: '真实运行' },
                        { value: 'official-mock', label: '官方 Mock' },
                        { value: 'replay', label: '脱敏回放' }
                      ]
                    : [{ value: 'real', label: '真实运行' }]
              }
            />
            <Select
              aria-label="核验结论"
              disabled={disabled}
              value={outcome}
              onChange={setOutcome}
              options={[
                { value: 'unknown', label: '结果未知' },
                { value: 'failed', label: '未通过' },
                { value: 'passed', label: '已核验通过' }
              ]}
            />
            <Button
              disabled={disabled || (outcome === 'passed' && checks.length !== item.checks.length)}
              onClick={() => void onSave(source, outcome, checks)}
            >
              保存核验记录
            </Button>
          </Space>
        </>
      ) : (
        <Typography.Text type="secondary">当前正式能力未使用此项，不记为通过。</Typography.Text>
      )}
    </Space>
  )
}

/** 原版本验收的四层记录入口；刷新和登记都不调用模型、工具或确认版本。 */
export default function AgentUiAcceptanceReview({
  workspace,
  disabled
}: {
  workspace: string
  disabled: boolean
}): ReactElement {
  const [open, setOpen] = useState(false)
  const [matrix, setMatrix] = useState<AcceptanceMatrix>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [refresh, setRefresh] = useState(0)
  const request = useRef<AbortController>()
  const saving = useRef(false)

  /** 每次打开或切换工程均重读；清理时丢弃迟到结果并关闭原订阅。 */
  useEffect(() => {
    setMatrix(undefined)
    setError('')
    if (!open || !workspace) return
    const controller = new AbortController()
    request.current = controller
    setBusy(true)
    void runAgentUiAcceptance(workspace, undefined, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setMatrix(value)
      })
      .catch((caught: unknown) => {
        if (!controller.signal.aborted)
          setError(caught instanceof Error ? caught.message : '读取失败')
      })
      .finally(() => {
        if (!controller.signal.aborted) setBusy(false)
      })
    return () => {
      controller.abort()
    }
  }, [open, workspace, refresh])

  /** 同帧提交锁避免重复保存；未知结果需重新读取，不能自动重试。 */
  const save = async (
    item: AcceptanceCase,
    source: AcceptanceSource,
    outcome: AcceptanceOutcome,
    checks: string[]
  ): Promise<void> => {
    if (saving.current) return
    saving.current = true
    const controller = request.current
    setBusy(true)
    setError('')
    try {
      const value = await runAgentUiAcceptance(
        workspace,
        { caseId: item.caseId, fingerprint: item.fingerprint, source, outcome, checks },
        controller?.signal
      )
      if (!controller?.signal.aborted) setMatrix(value)
    } catch (caught) {
      if (!controller?.signal.aborted) {
        setMatrix(undefined)
        setError(caught instanceof Error ? caught.message : '保存结果未知，请重新读取')
      }
    } finally {
      saving.current = false
      if (!controller?.signal.aborted) setBusy(false)
    }
  }
  return (
    <>
      <Button disabled={disabled || !workspace} onClick={() => setOpen(true)}>
        Agent UI 分层验收
      </Button>
      <Modal
        title="Agent UI 分层验收"
        open={open}
        width={760}
        onCancel={() => setOpen(false)}
        footer={null}
        destroyOnClose
        className={cx('agent-ui-acceptance-review')}
      >
        <Space direction="vertical" className={cx('agent-ui-acceptance-case')}>
          <Typography.Paragraph>
            请先在当前正式目标执行测试，再逐项登记实际观察。这里保存用户核验记录，不代为执行测试。全部核验后，仍需使用原“验收通过”动作确认版本。
          </Typography.Paragraph>
          {error && <Alert type="error" showIcon message={error} />}
          <Button disabled={busy} onClick={() => setRefresh((previous) => previous + 1)}>
            重新读取
          </Button>
          {busy && <Spin aria-label="正在读取或保存核验记录" />}
          {matrix &&
            (matrix.cases.length === 0 ? (
              <Empty description="当前应用没有需验收的共享 Agent UI" />
            ) : (
              <>
                <Typography.Text>
                  {matrix.ready
                    ? '全部适用用例已有通过记录，等待版本确认。'
                    : '仍有待核验、失效或未通过的用例。'}
                </Typography.Text>
                <Collapse>
                  {matrix.cases.map((item) => (
                    <Collapse.Panel
                      key={item.caseId}
                      header={`${LAYERS[item.layer]} · ${item.label} · ${item.pageLabel} · ${STATUS[item.status]}`}
                    >
                      <CaseReview
                        key={`${item.caseId}:${item.fingerprint}:${item.evidence?.checkedAt || ''}`}
                        item={item}
                        disabled={busy || disabled}
                        onSave={(source, outcome, checks) => save(item, source, outcome, checks)}
                      />
                    </Collapse.Panel>
                  ))}
                </Collapse>
              </>
            ))}
        </Space>
      </Modal>
    </>
  )
}
