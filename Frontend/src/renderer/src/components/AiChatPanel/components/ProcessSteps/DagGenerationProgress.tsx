import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  FileMarkdownOutlined,
  LoadingOutlined,
  NodeIndexOutlined
} from '@ant-design/icons'
import { Typography } from 'antd'
import { useEffect, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import type {
  DagGenerationSnapshot,
  DagGenerationStageRecord,
  DagGenerationTaskRecord
} from '../../../../service/agUiAgent'
import { cx } from '../../../../utils'
import './DagGenerationProgress.less'

const { Text } = Typography

type Props = {
  snapshot: DagGenerationSnapshot
}

/** 展示 DAG 生成阶段、最终拓扑任务和安全产物摘要。 */
export default function DagGenerationProgress({ snapshot }: Props): ReactElement {
  const running = snapshot.stages.some((stage) => stage.status === 'running')
  const failed = snapshot.stages.some((stage) => stage.status === 'failed')
  const wasRunning = useRef(running)
  const [open, setOpen] = useState(running)

  useEffect(() => {
    if (running) setOpen(true)
    else if (wasRunning.current) setOpen(false)
    wasRunning.current = running
  }, [running])

  return (
    <details
      className={cx('dag-generation', running ? 'running' : failed ? 'failed' : 'completed')}
      onToggle={(event) => setOpen(event.currentTarget.open)}
      open={open}
    >
      <summary className={cx('dag-generation-summary')}>
        <span className={cx('dag-generation-summary-icon')}>
          <NodeIndexOutlined />
        </span>
        <span className={cx('dag-generation-summary-copy')}>
          <Text strong>任务 DAG 生成明细</Text>
          <Text type="secondary">{dagGenerationSummary(snapshot, running, failed)}</Text>
        </span>
        <span className={cx('dag-generation-summary-metrics')} aria-hidden="true">
          <i>{snapshot.summary.unitCount} Units</i>
          <i>{snapshot.summary.taskCount} Tasks</i>
          <i>{snapshot.summary.batchCount} Batches</i>
        </span>
      </summary>

      <div className={cx('dag-generation-content')}>
        <ol className={cx('dag-generation-stages')} aria-label="任务 DAG 生成阶段">
          {snapshot.stages.map((stage, index) => (
            <li className={cx('dag-generation-stage', stage.status)} key={stage.id}>
              <span className={cx('dag-generation-stage-index')}>{index + 1}</span>
              <span className={cx('dag-generation-stage-icon')}>{stageIcon(stage)}</span>
              <span className={cx('dag-generation-stage-copy')}>
                <Text strong={stage.status === 'running'}>{stage.name}</Text>
                {stage.detail && (
                  <Text
                    className={cx('dag-generation-stage-detail')}
                    aria-live={stage.status === 'running' ? 'polite' : undefined}
                  >
                    {stage.detail}
                  </Text>
                )}
              </span>
              <Text className={cx('dag-generation-stage-status')}>
                {stageStatusLabel(stage.status)}
              </Text>
            </li>
          ))}
        </ol>

        {snapshot.tasks.length > 0 && (
          <section className={cx('dag-generation-tasks')} aria-label="已规划构建任务">
            <header>
              <span>
                <Text strong>已规划任务</Text>
                <Text type="secondary">按 DAG 拓扑顺序排列，将在下一阶段执行</Text>
              </span>
              <Text type="secondary">
                前端 {snapshot.summary.frontendCount} · 后端 {snapshot.summary.backendCount} · 数据库{' '}
                {snapshot.summary.databaseCount}
              </Text>
            </header>
            <ol>
              {snapshot.tasks.map((task, index) => (
                <DagTask index={index} key={task.id} task={task} />
              ))}
            </ol>
          </section>
        )}

        {snapshot.artifacts.length > 0 && (
          <section className={cx('dag-generation-artifacts')} aria-label="任务 DAG 产物">
            <Text strong>生成产物</Text>
            <ul>
              {snapshot.artifacts.map((artifact) => (
                <li key={artifact.id}>
                  <FileMarkdownOutlined />
                  <span>
                    <Text>{artifact.name}</Text>
                    <Text type="secondary">
                      {artifact.kind === 'internal' ? '内部状态已保存' : artifact.path || '已保存'}
                    </Text>
                  </span>
                  <CheckCircleOutlined />
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </details>
  )
}

/** 渲染单个已规划任务，默认收起文件和验收标准。 */
function DagTask({ index, task }: { index: number; task: DagGenerationTaskRecord }): ReactElement {
  return (
    <li>
      <details className={cx('dag-generation-task')}>
        <summary>
          <span className={cx('dag-generation-task-index')}>
            {String(index + 1).padStart(2, '0')}
          </span>
          <span className={cx('dag-generation-task-copy')}>
            <Text strong>{task.title}</Text>
            <Text type="secondary">
              {ownerLabel(task.owner)} · {dependencyLabel(task.dependencies)}
            </Text>
          </span>
          <Text className={cx('dag-generation-task-status', task.status)}>
            {taskStatusLabel(task.status)}
          </Text>
        </summary>
        <div className={cx('dag-generation-task-details')}>
          <TaskDetailList label="变更文件" values={task.changePaths} />
          <TaskDetailList label="验收标准" values={task.acceptanceCriteria} />
        </div>
      </details>
    </li>
  )
}

/** 渲染任务的文件或验收标准列表，并为缺省信息提供明确反馈。 */
function TaskDetailList({ label, values }: { label: string; values: string[] }): ReactElement {
  return (
    <section>
      <Text type="secondary">{label}</Text>
      {values.length > 0 ? (
        <ul>
          {values.map((value) => (
            <li key={value}>{value}</li>
          ))}
        </ul>
      ) : (
        <Text>未声明</Text>
      )}
    </section>
  )
}

/** 返回当前 DAG 卡片的紧凑摘要。 */
function dagGenerationSummary(
  snapshot: DagGenerationSnapshot,
  running: boolean,
  failed: boolean
): string {
  if (running) {
    return snapshot.stages.find((stage) => stage.status === 'running')?.name || '正在生成'
  }
  if (failed) return '生成未完成，请查看失败阶段'
  return `已生成 ${snapshot.summary.taskCount} 个任务`
}

/** 返回生成阶段对应的状态图标。 */
function stageIcon(stage: DagGenerationStageRecord): ReactElement {
  if (stage.status === 'running') return <LoadingOutlined spin />
  if (stage.status === 'completed') return <CheckCircleOutlined />
  if (stage.status === 'failed') return <CloseCircleOutlined />
  return <ClockCircleOutlined />
}

/** 返回生成阶段状态的中文标签。 */
function stageStatusLabel(status: DagGenerationStageRecord['status']): string {
  return {
    pending: '等待',
    running: '生成中',
    completed: '已完成',
    failed: '失败'
  }[status]
}

/** 返回任务所有者的用户可读名称。 */
function ownerLabel(owner: string): string {
  if (owner === 'frontend') return '前端生成'
  if (owner === 'backend' || owner === 'data_source') return '后端代码生成'
  if (owner === 'database') return '数据库任务'
  return owner || '未分配'
}

/** 返回任务依赖的紧凑文案。 */
function dependencyLabel(dependencies: string[]): string {
  return dependencies.length > 0 ? `依赖 ${dependencies.join('、')}` : '无前置依赖'
}

/** 返回计划任务状态的中文标签。 */
function taskStatusLabel(status: DagGenerationTaskRecord['status']): string {
  return {
    pending: '待执行',
    running: '执行中',
    completed: '已完成',
    failed: '失败'
  }[status]
}
