import {
  ApiOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  ClusterOutlined,
  CodeOutlined,
  DatabaseOutlined,
  DeploymentUnitOutlined,
  FileMarkdownOutlined,
  LoadingOutlined,
  NodeIndexOutlined,
  RightOutlined,
  SafetyCertificateOutlined,
  UnorderedListOutlined,
  WarningOutlined
} from '@ant-design/icons'
import { Typography } from 'antd'
import type { ReactElement, ReactNode } from 'react'
import type {
  DagGenerationSnapshot,
  DagGenerationStageOutput,
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

  return (
    <section className={cx('dag-generation', running ? 'running' : failed ? 'failed' : 'completed')}>
      <div className={cx('dag-generation-summary')}>
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
      </div>

      <div className={cx('dag-generation-content')}>
        <ol className={cx('dag-generation-stages')} aria-label="任务 DAG 生成阶段">
          {snapshot.stages.map((stage, index) => (
            <DagStage index={index} key={stage.id} stage={stage} />
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
    </section>
  )
}

/** 渲染单个已规划任务，保留任务节点可见，末级文件与验收细节默认收起。 */
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

/** 渲染单个 DAG 阶段，阶段节点可见，末级结构化产物默认收起。 */
function DagStage({ index, stage }: { index: number; stage: DagGenerationStageRecord }): ReactElement {
  const summaryInner = (
    <>
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
      <Text className={cx('dag-generation-stage-status')}>{stageStatusLabel(stage.status)}</Text>
    </>
  )

  if (!stage.output) {
    return (
      <li className={cx('dag-generation-stage', stage.status)}>
        <div className={cx('dag-generation-stage-summary', 'is-static')}>{summaryInner}</div>
      </li>
    )
  }

  return (
    <li className={cx('dag-generation-stage', stage.status, 'is-expandable')}>
      <details className={cx('dag-generation-stage-details')}>
        <summary className={cx('dag-generation-stage-summary')}>
          {summaryInner}
          <RightOutlined className={cx('dag-generation-stage-chevron')} />
        </summary>
        <div className={cx('dag-generation-stage-output')}>
          <StageOutput output={stage.output} />
        </div>
      </details>
    </li>
  )
}

/** 按阶段产物 kind 渲染对应结构化内容。 */
function StageOutput({ output }: { output: DagGenerationStageOutput }): ReactElement {
  switch (output.kind) {
    case 'unit_graph':
      return (
        <>
          <OutputMetrics
            items={[
              { label: 'Units', value: output.units.length, icon: <DeploymentUnitOutlined /> },
              { label: '依赖边', value: output.edges.length, icon: <NodeIndexOutlined /> }
            ]}
          />
          <OutputSection icon={<DeploymentUnitOutlined />} title="Unit 清单">
            <div className={cx('dag-generation-unit-grid')}>
              {output.units.map((unit) => (
                <span className={cx('dag-generation-data-chip')} key={unit.id}>
                  <code>{unit.id}</code>
                  <small>
                    {unit.kind} · {unit.status} · {unit.taskCount} tasks
                  </small>
                </span>
              ))}
            </div>
          </OutputSection>
          {output.edges.length > 0 ? (
            <OutputSection icon={<NodeIndexOutlined />} title="Unit 依赖">
              <EdgeList edges={output.edges} />
            </OutputSection>
          ) : null}
        </>
      )
    case 'build_context':
      return (
        <>
          <OutputMetrics
            items={[
              { label: '目标', value: output.target, icon: <CodeOutlined /> },
              { label: 'Units', value: output.units.length, icon: <DeploymentUnitOutlined /> },
              { label: 'Endpoints', value: output.endpoints.length, icon: <ApiOutlined /> },
              { label: '数据源', value: output.dataSources.length, icon: <DatabaseOutlined /> }
            ]}
          />
          <OutputSection icon={<DeploymentUnitOutlined />} title="涉及 Unit">
            <IdList values={output.units} />
          </OutputSection>
          <OutputSection icon={<ApiOutlined />} title="关联 Endpoint / API 契约">
            <IdList values={output.endpoints} />
          </OutputSection>
          <OutputSection icon={<DatabaseOutlined />} title="数据源与可复用任务">
            <IdList values={output.dataSources} />
          </OutputSection>
        </>
      )
    case 'contract_validation':
      return (
        <>
          <ValidationBanner
            passed={output.passed}
            text={
              output.passed
                ? '页面依赖与 API 契约校验通过'
                : `发现 ${output.issues.length} 个校验问题`
            }
          />
          <OutputSection icon={<ApiOutlined />} title="校验范围">
            <IdList values={output.scope} />
          </OutputSection>
          {output.issues.length > 0 ? (
            <OutputSection icon={<WarningOutlined />} title="问题详情">
              <ul className={cx('dag-generation-bullet-list')}>
                {output.issues.map((issue) => (
                  <li key={issue}>{issue}</li>
                ))}
              </ul>
            </OutputSection>
          ) : null}
        </>
      )
    case 'tasks': {
      const counts = output.tasks.reduce(
        (acc, task) => {
          const owner = task.owner === 'backend' || task.owner === 'data_source' ? 'backend' : task.owner
          acc[owner] = (acc[owner] || 0) + 1
          return acc
        },
        {} as Record<string, number>
      )
      return (
        <>
          <OutputMetrics
            items={[
              { label: '任务', value: output.tasks.length, icon: <UnorderedListOutlined /> },
              { label: '前端', value: counts.frontend || 0, icon: <CodeOutlined /> },
              { label: '后端', value: counts.backend || 0, icon: <ClusterOutlined /> },
              { label: '数据库', value: counts.database || 0, icon: <DatabaseOutlined /> }
            ]}
          />
          <ol className={cx('dag-generation-tasks', 'inline')}>
            {output.tasks.map((task, taskIndex) => (
              <DagTask index={taskIndex} key={task.id} task={task} />
            ))}
          </ol>
          {output.edges.length > 0 ? (
            <OutputSection icon={<NodeIndexOutlined />} title="任务依赖">
              <EdgeList edges={output.edges} />
            </OutputSection>
          ) : null}
        </>
      )
    }
    case 'dag_validation':
      return (
        <>
          <ValidationBanner
            passed={output.valid}
            text={output.valid ? '任务 DAG 校验通过' : `发现 ${output.batches.length} 个批次问题`}
          />
          <OutputMetrics
            items={[
              { label: '执行批次', value: output.batches.length, icon: <UnorderedListOutlined /> },
              { label: '拓扑节点', value: output.topologicalOrder.length, icon: <NodeIndexOutlined /> }
            ]}
          />
          <OutputSection icon={<UnorderedListOutlined />} title="执行批次">
            <ol className={cx('dag-generation-batches')}>
              {output.batches.map((batch, batchIndex) => (
                <li key={batchIndex}>
                  <span className={cx('dag-generation-batch-index')}>
                    {String(batchIndex + 1).padStart(2, '0')}
                  </span>
                  <span>
                    <Text strong>{batchModeLabel(batch.mode)}</Text>
                    <Text type="secondary">{batch.taskIds.join('、')}</Text>
                  </span>
                </li>
              ))}
            </ol>
          </OutputSection>
          <OutputSection icon={<NodeIndexOutlined />} title="拓扑顺序">
            <IdList values={output.topologicalOrder} />
          </OutputSection>
        </>
      )
    case 'artifacts':
      return (
        <OutputSection icon={<FileMarkdownOutlined />} title="生成产物">
          <ul className={cx('dag-generation-artifacts', 'inline')}>
            {output.artifacts.map((artifact) => (
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
        </OutputSection>
      )
  }
}

/** 渲染阶段产物顶部的关键指标。 */
function OutputMetrics({
  items
}: {
  items: Array<{ label: string; value: number | string; icon: ReactElement }>
}): ReactElement {
  return (
    <div className={cx('dag-generation-output-metrics')}>
      {items.map((item, index) => (
        <span className={cx('dag-generation-output-metric')} key={index}>
          <span className={cx('dag-generation-output-metric-icon')}>{item.icon}</span>
          <strong>{item.value}</strong>
          <small>{item.label}</small>
        </span>
      ))}
    </div>
  )
}

/** 渲染阶段产物的一个分区块（标题 + 内容）。 */
function OutputSection({
  icon,
  title,
  children
}: {
  icon: ReactElement
  title: string
  children: ReactNode
}): ReactElement {
  return (
    <section className={cx('dag-generation-output-section')}>
      <header>
        <span className={cx('dag-generation-output-section-icon')}>{icon}</span>
        <Text strong>{title}</Text>
      </header>
      <div className={cx('dag-generation-output-section-body')}>{children}</div>
    </section>
  )
}

/** 渲染 ID 列表（接口、Unit、数据源等）。 */
function IdList({ values }: { values: string[] }): ReactElement {
  if (values.length === 0) return <Text type="secondary">无</Text>
  return (
    <ul className={cx('dag-generation-id-list')}>
      {values.map((value) => (
        <li key={value}>
          <code>{value}</code>
        </li>
      ))}
    </ul>
  )
}

/** 渲染依赖边列表（from → to）。 */
function EdgeList({ edges }: { edges: Array<{ from: string; to: string }> }): ReactElement {
  if (edges.length === 0) return <Text type="secondary">无</Text>
  return (
    <ul className={cx('dag-generation-edge-list')}>
      {edges.map((edge, index) => (
        <li key={index}>
          <code>{edge.from}</code>
          <span className={cx('dag-generation-edge-arrow')}>→</span>
          <code>{edge.to}</code>
        </li>
      ))}
    </ul>
  )
}

/** 渲染契约/DAG 校验结果横条。 */
function ValidationBanner({ passed, text }: { passed: boolean; text: string }): ReactElement {
  return (
    <div className={cx('dag-generation-validation-banner', passed ? 'valid' : 'invalid')}>
      {passed ? <SafetyCertificateOutlined /> : <WarningOutlined />}
      <span>{text}</span>
    </div>
  )
}

/** 返回执行批次的中文标签。 */
function batchModeLabel(mode: 'parallel' | 'blocked' | 'serial'): string {
  if (mode === 'parallel') return '并行批次'
  if (mode === 'blocked') return '阻塞批次'
  return '串行批次'
}
