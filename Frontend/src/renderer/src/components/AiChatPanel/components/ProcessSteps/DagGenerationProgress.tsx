import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  LoadingOutlined,
  NodeIndexOutlined,
  StopOutlined,
  WarningOutlined
} from '@ant-design/icons'
import { Typography } from 'antd'
import { useEffect, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import type {
  DagGenerationPhase,
  DagGenerationSnapshot,
  DagGenerationUnitRecord,
  DagGenerationUnitStatus
} from '../../../../service/agUiAgent'
import { cx } from '../../../../utils'
import {
  dagGenerationStrategyLabel,
  dagGenerationSummaryCopy,
  dagGenerationUnitAttemptCopy,
  dagGenerationUnitStatusLabel,
  dagGenerationUnitTaskCopy
} from '../../stageOutputState'
import './DagGenerationProgress.less'

const { Text } = Typography

type Props = {
  snapshot: DagGenerationSnapshot
}

/** 按 PlanningRun 的真实 Unit 状态展示生成、校验、重试和 Global repair。 */
export default function DagGenerationProgress({ snapshot }: Props): ReactElement {
  const active = snapshot.status === 'active'
  const wasActive = useRef(active)
  const [open, setOpen] = useState(active)

  useEffect(() => {
    if (active) setOpen(true)
    else if (wasActive.current) setOpen(false)
    wasActive.current = active
  }, [active])

  return (
    <details
      className={cx('dag-generation', snapshot.status)}
      onToggle={(event) => {
        if (active && !event.currentTarget.open) {
          event.currentTarget.open = true
          return
        }
        setOpen(event.currentTarget.open)
      }}
      open={open}
    >
      <summary className={cx('dag-generation-summary')}>
        <span className={cx('dag-generation-summary-icon')}>
          <NodeIndexOutlined />
        </span>
        <span className={cx('dag-generation-summary-copy')}>
          <Text strong>任务 DAG Unit 进度</Text>
          <Text type="secondary">{dagGenerationSummaryCopy(snapshot)}</Text>
        </span>
        <span className={cx('dag-generation-summary-metrics')}>
          <i>
            {snapshot.summary.readyUnitCount}/{snapshot.summary.unitCount} ready
          </i>
          <i>{snapshot.summary.retainedTaskCount} retained</i>
          <i>{snapshot.summary.candidateTaskCount} candidate</i>
        </span>
      </summary>

      <div className={cx('dag-generation-content')}>
        <div className={cx('dag-generation-run-facts')}>
          <code>{dagGenerationPhaseLabel(snapshot.phase)}</code>
          <code>revision {snapshot.revision}</code>
          {snapshot.globalRepairRound > 0 ? (
            <code className={cx('global-repair')}>
              Global repair {snapshot.globalRepairRound}/{snapshot.globalRepairLimit}
            </code>
          ) : null}
        </div>
        <ol className={cx('dag-generation-units')} aria-label="任务 DAG Unit 进度">
          {snapshot.units.map((unit) => (
            <DagGenerationUnit key={unit.id} unit={unit} />
          ))}
        </ol>
        {snapshot.globalIssues.length > 0 ? (
          <section className={cx('dag-generation-global-issues')}>
            <Text strong>Global issues</Text>
            <ul>
              {snapshot.globalIssues.map((issue) => (
                <li key={`${issue.code}-${issue.message}`}>{issue.message}</li>
              ))}
            </ul>
          </section>
        ) : null}
      </div>
    </details>
  )
}

/** 渲染单个 Unit 的离散状态、真实 attempt 和安全计数。 */
function DagGenerationUnit({ unit }: { unit: DagGenerationUnitRecord }): ReactElement {
  const statusLabel = dagGenerationUnitStatusLabel(unit)
  const attemptCopy = dagGenerationUnitAttemptCopy(unit)
  const taskCopy = dagGenerationUnitTaskCopy(unit)
  return (
    <li className={cx('dag-generation-unit', unit.status)}>
      <span className={cx('dag-generation-unit-icon')}>{dagGenerationUnitIcon(unit.status)}</span>
      <span className={cx('dag-generation-unit-copy')}>
        <Text strong>{unit.id}</Text>
        <Text type="secondary">
          {dagGenerationUnitKindLabel(unit.kind)} · {dagGenerationStrategyLabel(unit)}
        </Text>
        {unit.issues.length > 0 ? (
          <Text className={cx('dag-generation-unit-issue')}>
            {unit.issues.map((issue) => issue.message).join('；')}
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
    }[kind] ||
    kind ||
    '未知'
  )
}

/** 返回 PlanningRun 阶段的稳定展示标签。 */
function dagGenerationPhaseLabel(phase: DagGenerationPhase): string {
  return {
    preparing: 'preparing',
    generating_units: 'generating_units',
    global_check: 'global_check',
    assembling: 'assembling',
    validating: 'validating',
    persisting_pending: 'persisting_pending'
  }[phase]
}
