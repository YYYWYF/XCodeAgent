import {
  ArrowRightOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  DownOutlined,
  LoadingOutlined,
  NodeIndexOutlined,
  StopOutlined,
  UpOutlined,
  WarningOutlined
} from '@ant-design/icons'
import { Button, Typography } from 'antd'
import { useEffect, useRef, useState } from 'react'
import type { ReactElement, SyntheticEvent } from 'react'
import type {
  DagGenerationPhase,
  DagGenerationSnapshot,
  DagGenerationUnitRecord,
  DagGenerationUnitStatus
} from '../../../../service/agUiAgent'
import { cx } from '../../../../utils'
import type { DagGenerationCompletionSummary } from '../../dagPendingPlanPresentation'
import {
  dagGenerationIssueMessage,
  dagGenerationStrategyLabel,
  dagGenerationSummaryCopy,
  dagGenerationUnitAttemptCopy,
  dagGenerationUnitDisplayName,
  dagGenerationUnitStatusLabel,
  dagGenerationUnitTaskCopy
} from '../../stageOutputState'
import './DagGenerationProgress.less'
import './ToolActivityChain.less'

const { Text } = Typography

type Props = {
  snapshot?: DagGenerationSnapshot
  completion?: {
    summary?: DagGenerationCompletionSummary
    onGoToConfirmation?: () => void
  }
}

/** 按真实 Unit 状态展示生成过程，待确认时改为完成摘要并保留可展开记录。 */
export default function DagGenerationProgress({
  snapshot,
  completion
}: Props): ReactElement | null {
  const active = snapshot?.status === 'active' && !completion
  const wasActive = useRef(active)
  const manuallyToggled = useRef(false)
  const planningRunId = useRef(snapshot?.planningRunId)
  const [open, setOpen] = useState(active)

  useEffect(() => {
    // 新规划使用默认展开策略；同一规划内，用户选择优先于快照和 Pending 展示变化。
    if (planningRunId.current !== snapshot?.planningRunId) {
      planningRunId.current = snapshot?.planningRunId
      manuallyToggled.current = false
      setOpen(active)
    } else if (!manuallyToggled.current) {
      if (active) setOpen(true)
      else if (wasActive.current) setOpen(false)
    }
    wasActive.current = active
  }, [active, snapshot?.planningRunId])

  /** 记录鼠标、键盘及辅助功能触发的折叠选择，忽略嵌套元素与程序同步事件。 */
  const handleToggle = (event: SyntheticEvent<HTMLDetailsElement>): void => {
    if (event.target !== event.currentTarget) return
    const nextOpen = event.currentTarget.open
    if (nextOpen !== open) manuallyToggled.current = true
    setOpen(nextOpen)
  }

  if (completion) {
    return (
      <section className={cx('dag-generation', 'completed')}>
        <header className={cx('dag-generation-completion-header')}>
          <span className={cx('dag-generation-summary-icon')}>
            <CheckCircleOutlined />
          </span>
          <span className={cx('dag-generation-completion-copy')}>
            <Text strong>任务生成完成</Text>
            <Text type="secondary">任务计划已准备好，请确认后继续开发。</Text>
          </span>
          {completion.summary ? (
            <span className={cx('dag-generation-summary-metrics')}>
              <i>
                {completion.summary.readyUnitCount}/{completion.summary.unitCount} 项工作已就绪
              </i>
              <i>新增 {completion.summary.addedTaskCount} 项任务</i>
              {completion.summary.reusedTaskCount > 0 ? (
                <i>复用 {completion.summary.reusedTaskCount} 项任务</i>
              ) : null}
            </span>
          ) : null}
          {completion.onGoToConfirmation ? (
            <Button
              className={cx('dag-generation-go-confirmation')}
              icon={<ArrowRightOutlined />}
              onClick={completion.onGoToConfirmation}
              size="small"
              type="primary"
            >
              去确认
            </Button>
          ) : null}
        </header>
        {snapshot ? (
          <details
            className={cx('dag-generation-details', snapshot.status)}
            onToggle={handleToggle}
            open={open}
          >
            <summary>{completion.summary ? '查看生成明细' : '查看生成过程记录'}</summary>
            <DagGenerationDetails historical snapshot={snapshot} />
          </details>
        ) : null}
      </section>
    )
  }

  if (!snapshot) return null

  return (
    <details className={cx('dag-generation', snapshot.status)} onToggle={handleToggle} open={open}>
      <summary className={cx('dag-generation-summary')}>
        <span className={cx('dag-generation-summary-icon')}>
          <NodeIndexOutlined />
        </span>
        <span className={cx('dag-generation-summary-copy')}>
          <Text strong>任务生成进度</Text>
          <Text type="secondary">{dagGenerationSummaryCopy(snapshot)}</Text>
        </span>
        <span className={cx('dag-generation-summary-metrics')}>
          <i>
            {snapshot.summary.readyUnitCount}/{snapshot.summary.unitCount} 项工作就绪
          </i>
          <i>{snapshot.summary.retainedTaskCount} 项复用任务</i>
          <i>{snapshot.summary.candidateTaskCount} 项新增任务</i>
        </span>
        <span
          className={cx('tool-activity-chain-toggle', 'dag-generation-toggle')}
          title={open ? '收起详情' : '展开详情'}
          aria-hidden="true"
        >
          {open ? <UpOutlined aria-hidden="true" /> : <DownOutlined aria-hidden="true" />}
        </span>
      </summary>

      <DagGenerationDetails snapshot={snapshot} />
    </details>
  )
}

/** 保留 Unit、尝试次数及全局校验的原始可展开明细。 */
function DagGenerationDetails({
  historical = false,
  snapshot
}: {
  historical?: boolean
  snapshot: DagGenerationSnapshot
}): ReactElement {
  return (
    <div className={cx('dag-generation-content')}>
      <div className={cx('dag-generation-run-facts')}>
        <code>
          {historical ? '生成过程记录：' : ''}
          {dagGenerationPhaseLabel(snapshot.phase)}
        </code>
        {snapshot.globalRepairRound > 0 ? (
          <code className={cx('global-repair')}>
            全局修复 第 {snapshot.globalRepairRound}/{snapshot.globalRepairLimit} 轮
          </code>
        ) : null}
      </div>
      <ol className={cx('dag-generation-units')} aria-label="任务生成进度">
        {snapshot.units.map((unit, index) => (
          <DagGenerationUnit index={index} key={unit.id} unit={unit} units={snapshot.units} />
        ))}
      </ol>
      {snapshot.globalIssues.length > 0 ? (
        <section className={cx('dag-generation-global-issues')}>
          <Text strong>全局校验问题</Text>
          <ul>
            {snapshot.globalIssues.map((issue) => (
              <li key={`${issue.code}-${issue.message}`}>
                {dagGenerationIssueMessage(issue.message, snapshot.units)}
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  )
}

/** 渲染单个 Unit 的离散状态、真实 attempt 和安全计数。 */
function DagGenerationUnit({
  index,
  unit,
  units
}: {
  index: number
  unit: DagGenerationUnitRecord
  units: DagGenerationUnitRecord[]
}): ReactElement {
  const statusLabel = dagGenerationUnitStatusLabel(unit)
  const attemptCopy = dagGenerationUnitAttemptCopy(unit)
  const taskCopy = dagGenerationUnitTaskCopy(unit)
  return (
    <li className={cx('dag-generation-unit', unit.status)}>
      <span className={cx('dag-generation-unit-icon')}>{dagGenerationUnitIcon(unit.status)}</span>
      <span className={cx('dag-generation-unit-copy')}>
        <Text strong>{dagGenerationUnitDisplayName(unit, index, units)}</Text>
        <Text type="secondary">
          {dagGenerationUnitKindLabel(unit.kind)} · {dagGenerationStrategyLabel(unit)}
        </Text>
        {unit.issues.length > 0 ? (
          <Text className={cx('dag-generation-unit-issue')}>
            {unit.issues.map((issue) => dagGenerationIssueMessage(issue.message, units)).join('；')}
          </Text>
        ) : null}
      </span>
      <span className={cx('dag-generation-unit-facts')}>
        <code className={cx('status')}>{statusLabel}</code>
        {attemptCopy ? <code>{attemptCopy}</code> : null}
        {taskCopy ? <code>{taskCopy}</code> : null}
      </span>
    </li>
  )
}

/** 返回 Unit 状态图标。 */
function dagGenerationUnitIcon(status: DagGenerationUnitStatus): ReactElement {
  if (status === 'generating' || status === 'validating') return <LoadingOutlined spin />
  if (status === 'candidate_ready' || status === 'not_required') return <CheckCircleOutlined />
  if (status === 'round_exhausted') return <WarningOutlined />
  if (status === 'aborted') return <StopOutlined />
  if (status === 'pending') return <ClockCircleOutlined />
  return <CloseCircleOutlined />
}

/** 返回 Unit 类型的紧凑中文名称。 */
function dagGenerationUnitKindLabel(kind: string): string {
  return (
    {
      application: '应用',
      frontend: '前端',
      backend: '后端',
      database: '数据库',
      page: '页面',
      app: '应用集成',
      authorization: '权限'
    }[kind] || '开发任务'
  )
}

/** 返回 PlanningRun 阶段的稳定展示标签。 */
function dagGenerationPhaseLabel(phase: DagGenerationPhase): string {
  return {
    preparing: '准备中',
    generating_units: '正在生成开发任务',
    global_check: '正在进行全局校验',
    assembling: '正在汇总开发任务',
    validating: '正在校验执行计划',
    persisting_pending: '正在保存待确认计划'
  }[phase]
}
