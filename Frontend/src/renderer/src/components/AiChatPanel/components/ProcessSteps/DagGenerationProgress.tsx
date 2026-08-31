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
import { useEffect, useRef, useState } from 'react'
import type { ReactElement, ReactNode } from 'react'
import type {
  DagGenerationArtifactRecord,
  DagGenerationBatchRecord,
  DagGenerationEdgeList,
  DagGenerationSnapshot,
  DagGenerationStageOutput,
  DagGenerationStageRecord,
  DagGenerationTaskRecord
} from '../../../../service/agUiAgent'
import { cx } from '../../../../utils'
import './DagGenerationProgress.less'

const { Text } = Typography

type Props = {
  onStageSelect?: (stageId: string) => void
  selectedStageId?: string
  snapshot: DagGenerationSnapshot
}

/** 展示 DAG 生成阶段、进度和兼容摘要，详细产物统一交给右侧面板。 */
export default function DagGenerationProgress({
  onStageSelect,
  selectedStageId,
  snapshot
}: Props): ReactElement {
  const running = snapshot.stages.some((stage) => stage.status === 'running')
  const failed = snapshot.stages.some((stage) => stage.status === 'failed')
  const pending = snapshot.stages.some((stage) => stage.status === 'pending')
  // 子阶段完成与下一阶段启动之间仍属于生成中，不能把累计旧任务误报成本轮已生成。
  const generating = !failed && (running || pending)
  const wasRunning = useRef(generating)
  const [open, setOpen] = useState(generating)

  useEffect(() => {
    if (generating) setOpen(true)
    else if (wasRunning.current) setOpen(false)
    wasRunning.current = generating
  }, [generating])

  return (
    <details
      className={cx('dag-generation', generating ? 'running' : failed ? 'failed' : 'completed')}
      onToggle={(event) => {
        if (generating && !event.currentTarget.open) {
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
          <Text strong>任务 DAG 生成明细</Text>
          <Text type="secondary">{dagGenerationSummary(snapshot, generating, failed)}</Text>
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
            <DagGenerationStage
              index={index}
              key={stage.id}
              onSelect={onStageSelect}
              selected={stage.id === selectedStageId}
              stage={stage}
            />
          ))}
        </ol>
      </div>
    </details>
  )
}

/** 渲染单个阶段进度；已有产物可回看，运行中阶段可点击恢复自动跟随。 */
function DagGenerationStage({
  index,
  onSelect,
  selected,
  stage
}: {
  index: number
  onSelect?: (stageId: string) => void
  selected: boolean
  stage: DagGenerationStageRecord
}): ReactElement {
  const selectable = Boolean(onSelect && (stage.output || stage.status === 'running'))
  const selectionTitle =
    stage.status === 'running'
      ? `切回${stage.name}并自动跟随`
      : stage.output
        ? `在右侧查看${stage.name}产物`
        : undefined
  return (
    <li className={cx('dag-generation-stage', stage.status, selected && 'selected')}>
      <span className={cx('dag-generation-stage-index')}>{index + 1}</span>
      <span className={cx('dag-generation-stage-icon')}>{stageIcon(stage)}</span>
      <button
        className={cx('dag-generation-stage-summary')}
        disabled={!selectable}
        onClick={() => onSelect?.(stage.id)}
        title={selectable ? selectionTitle : undefined}
        type="button"
      >
        <span className={cx('dag-generation-stage-copy')}>
          <Text strong={stage.status === 'running'}>{stage.name}</Text>
          <Text
            className={cx('dag-generation-stage-detail')}
            aria-live={stage.status === 'running' ? 'polite' : undefined}
          >
            {stage.detail || '尚无阶段说明'}
          </Text>
        </span>
      </button>
      <Text className={cx('dag-generation-stage-status')}>{stageStatusLabel(stage.status)}</Text>
    </li>
  )
}

/** 根据阶段产物类型渲染结构化详情。 */
export function StageOutput({ stage }: { stage: DagGenerationStageRecord }): ReactElement {
  if (!stage.output) {
    return (
      <div className={cx('dag-generation-output-empty')}>
        <ClockCircleOutlined />
        <Text type="secondary">
          {stage.status === 'failed' ? '该步骤未生成可用产物' : '详细产物将在步骤完成后生成'}
        </Text>
      </div>
    )
  }

  switch (stage.output.kind) {
    case 'unit_graph':
      return <UnitGraphOutput output={stage.output} />
    case 'build_context':
      return <BuildContextOutput output={stage.output} />
    case 'contract_validation':
      return <ContractValidationOutput output={stage.output} />
    case 'candidate_tasks':
      return <TaskPlanOutput label="候选任务详情" output={stage.output} />
    case 'compiled_tasks':
      return <TaskPlanOutput label="已规划任务" output={stage.output} />
    case 'dag_validation':
      return <DagValidationOutput output={stage.output} />
    case 'artifacts':
      return <ArtifactOutput output={stage.output} />
  }
}

/** 展示 Unit 列表和 Unit 依赖边。 */
function UnitGraphOutput({
  output
}: {
  output: Extract<DagGenerationStageOutput, { kind: 'unit_graph' }>
}): ReactElement {
  return (
    <div className={cx('dag-generation-output-stack')}>
      <OutputMetrics
        items={[
          { label: 'Units', value: output.units.length, icon: <DeploymentUnitOutlined /> },
          { label: '依赖边', value: output.edges.items.length, icon: <NodeIndexOutlined /> },
          { label: '骨架', value: output.reused ? '复用' : '新建', icon: <ClusterOutlined /> }
        ]}
      />
      <OutputSection icon={<DeploymentUnitOutlined />} title="Unit 清单">
        <div className={cx('dag-generation-unit-grid')}>
          {output.units.map((unit) => (
            <span className={cx('dag-generation-data-chip')} key={unit.id}>
              <code>{unit.id}</code>
              <small>
                {unitKindLabel(unit.kind)} · {unitStatusLabel(unit.status)} · {unit.taskCount} tasks
              </small>
            </span>
          ))}
        </div>
      </OutputSection>
      <EdgeSection edges={output.edges} title="Unit 依赖" />
      <ValidationNotice validation={output.validation} />
    </div>
  )
}

/** 展示当前目标的构建上下文。 */
function BuildContextOutput({
  output
}: {
  output: Extract<DagGenerationStageOutput, { kind: 'build_context' }>
}): ReactElement {
  return (
    <div className={cx('dag-generation-output-stack')}>
      <OutputMetrics
        items={[
          {
            label: '目标',
            value: `${output.target.type}:${output.target.id}`,
            icon: <CodeOutlined />
          },
          {
            label: 'Units',
            value: output.requiredUnitIds.length,
            icon: <DeploymentUnitOutlined />
          },
          { label: 'Endpoints', value: output.endpointIds.length, icon: <ApiOutlined /> },
          { label: '数据库', value: output.databaseStatus, icon: <DatabaseOutlined /> }
        ]}
      />
      <OutputSection icon={<DeploymentUnitOutlined />} title="涉及 Unit">
        <IdList values={output.requiredUnitIds} empty="未解析到定向 Unit" />
      </OutputSection>
      <OutputSection icon={<ApiOutlined />} title="关联 Endpoint / API Contract">
        <IdList values={[...output.endpointIds, ...output.apiContractIds]} empty="无关联接口" />
      </OutputSection>
      <OutputSection icon={<DatabaseOutlined />} title="数据源与可复用任务">
        <IdList
          values={[...output.dataSourceIds, ...output.reusableTaskIds]}
          empty="无额外数据源或可复用任务"
        />
      </OutputSection>
    </div>
  )
}

/** 展示契约校验结果及受限问题列表。 */
function ContractValidationOutput({
  output
}: {
  output: Extract<DagGenerationStageOutput, { kind: 'contract_validation' }>
}): ReactElement {
  const passed = output.isValid && output.issues.length === 0
  return (
    <div className={cx('dag-generation-output-stack')}>
      <div className={cx('dag-generation-validation-banner', passed ? 'valid' : 'invalid')}>
        {passed ? <SafetyCertificateOutlined /> : <WarningOutlined />}
        <span>
          {passed ? '页面依赖与 API 契约校验通过' : `发现 ${output.issues.length} 个校验问题`}
        </span>
      </div>
      <OutputSection icon={<ApiOutlined />} title="校验范围">
        <IdList
          values={[...output.checkedEndpointIds, ...output.checkedApiContractIds]}
          empty="未发现可校验的 Endpoint 或 API Contract"
        />
      </OutputSection>
      {output.issues.length > 0 && (
        <OutputSection icon={<WarningOutlined />} title="问题详情">
          <BulletList values={output.issues} />
        </OutputSection>
      )}
    </div>
  )
}

/** 展示候选任务或最终编译任务，最终任务表归属步骤 5。 */
function TaskPlanOutput({
  label,
  output
}: {
  label: string
  output: Extract<DagGenerationStageOutput, { kind: 'candidate_tasks' | 'compiled_tasks' }>
}): ReactElement {
  return (
    <section className={cx('dag-generation-tasks')} aria-label={label}>
      <header>
        <span>
          <Text strong>{label}</Text>
          <Text type="secondary">
            {output.kind === 'compiled_tasks'
              ? '按 DAG 拓扑顺序排列，将在下一阶段执行'
              : '任务规划模型返回的候选构建项'}
          </Text>
        </span>
        <Text type="secondary">
          前端 {output.summary.frontend} · 后端 {output.summary.backend} · 数据库{' '}
          {output.summary.database}
        </Text>
      </header>
      {output.tasks.length > 0 ? (
        <ol>
          {output.tasks.map((task, index) => (
            <DagTask index={index} key={task.id} task={task} />
          ))}
        </ol>
      ) : (
        <div className={cx('dag-generation-output-empty')}>
          <UnorderedListOutlined />
          <Text type="secondary">尚未生成任务</Text>
        </div>
      )}
      {output.kind === 'compiled_tasks' && <EdgeSection edges={output.edges} title="任务依赖" />}
    </section>
  )
}

/** 展示 DAG 校验、拓扑顺序和执行批次。 */
function DagValidationOutput({
  output
}: {
  output: Extract<DagGenerationStageOutput, { kind: 'dag_validation' }>
}): ReactElement {
  return (
    <div className={cx('dag-generation-output-stack')}>
      <div className={cx('dag-generation-validation-banner', output.isValid ? 'valid' : 'invalid')}>
        {output.isValid ? <CheckCircleOutlined /> : <WarningOutlined />}
        <span>
          {output.isValid ? '任务 DAG 校验通过' : `发现 ${output.issues.length} 个 DAG 问题`}
        </span>
      </div>
      <OutputMetrics
        items={[
          { label: '根任务', value: output.roots.length, icon: <NodeIndexOutlined /> },
          { label: '叶任务', value: output.leaves.length, icon: <DeploymentUnitOutlined /> },
          { label: '执行批次', value: output.batches.length, icon: <UnorderedListOutlined /> }
        ]}
      />
      <OutputSection icon={<UnorderedListOutlined />} title="执行批次">
        {output.batches.length > 0 ? (
          <ol className={cx('dag-generation-batches')}>
            {output.batches.map((batch) => (
              <BatchRow batch={batch} key={`${batch.index}-${batch.taskIds.join(',')}`} />
            ))}
          </ol>
        ) : (
          <Text type="secondary">尚未形成执行批次</Text>
        )}
      </OutputSection>
      <OutputSection icon={<NodeIndexOutlined />} title="拓扑顺序">
        <IdList values={output.topologicalOrder} empty="暂无拓扑顺序" />
      </OutputSection>
      {output.issues.length > 0 && (
        <OutputSection icon={<WarningOutlined />} title="问题详情">
          <BulletList values={output.issues} />
        </OutputSection>
      )}
    </div>
  )
}

/** 展示步骤 7 保存的用户可读产物。 */
function ArtifactOutput({
  output
}: {
  output: Extract<DagGenerationStageOutput, { kind: 'artifacts' }>
}): ReactElement {
  return (
    <section className={cx('dag-generation-artifacts')} aria-label="任务 DAG 产物">
      <header>
        <span>
          <Text strong>生成产物</Text>
          <Text type="secondary">内部计划状态与 Markdown DAG 已完成保存</Text>
        </span>
        <Text type="secondary">{output.count} 项</Text>
      </header>
      <ul>
        {output.artifacts.map((artifact) => (
          <ArtifactRow artifact={artifact} key={artifact.id} />
        ))}
      </ul>
    </section>
  )
}

/** 渲染单个任务，默认收起文件、交付物和两类验收检查。 */
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
          <TaskDetailList
            label="任务说明"
            values={task.description?.trim() ? [task.description.trim()] : []}
          />
          <TaskDetailList label="变更文件" values={task.changePaths} />
          <TaskDetailList label="允许范围" values={task.allowedPaths || []} />
          <TaskDetailList label="交付物" values={recordDescriptions(task.deliverables || [])} />
          <TaskDetailList
            label="工程检查"
            values={checkDescriptions(task.engineeringAcceptanceChecks)}
          />
          <TaskDetailList
            label="业务检查"
            values={checkDescriptions(task.businessAcceptanceChecks)}
          />
        </div>
      </details>
    </li>
  )
}

/** 从结构化检查提取安全的用户可读描述，不把业务检查误称为验收标准。 */
function checkDescriptions(value: Array<Record<string, unknown>>): string[] {
  return value.flatMap((item) => {
    const description = typeof item.description === 'string' ? item.description.trim() : ''
    const kind = typeof item.kind === 'string' ? item.kind.trim() : ''
    if (!description) return []
    return [kind ? `${kind}：${description}` : description]
  })
}

/** 从结构化交付物中提取名称或描述，避免右侧任务详情退化为原始 JSON。 */
function recordDescriptions(value: Array<Record<string, unknown>>): string[] {
  return value.flatMap((item) => {
    const label = ['description', 'name', 'title', 'path']
      .map((key) => (typeof item[key] === 'string' ? item[key].trim() : ''))
      .find(Boolean)
    return label ? [label] : []
  })
}

/** 渲染任务的文件或结构化检查列表，并为缺省信息提供明确反馈。 */
function TaskDetailList({ label, values }: { label: string; values: string[] }): ReactElement {
  return (
    <section>
      <Text type="secondary">{label}</Text>
      {values.length > 0 ? <BulletList values={values} /> : <Text>未声明</Text>}
    </section>
  )
}

/** 渲染统一的指标小卡片。 */
function OutputMetrics({
  items
}: {
  items: Array<{ label: string; value: ReactNode; icon: ReactNode }>
}): ReactElement {
  return (
    <div className={cx('dag-generation-output-metrics')}>
      {items.map((item) => (
        <span key={item.label}>
          <i>{item.icon}</i>
          <small>{item.label}</small>
          <strong>{item.value}</strong>
        </span>
      ))}
    </div>
  )
}

/** 渲染带图标的详情分区。 */
function OutputSection({
  icon,
  title,
  children
}: {
  icon: ReactNode
  title: string
  children: ReactNode
}): ReactElement {
  return (
    <section className={cx('dag-generation-output-section')}>
      <header>
        <span>{icon}</span>
        <Text strong>{title}</Text>
      </header>
      <div>{children}</div>
    </section>
  )
}

/** 渲染 ID 芯片列表。 */
function IdList({ values, empty }: { values: string[]; empty: string }): ReactElement {
  const uniqueValues = [...new Set(values)]
  return uniqueValues.length > 0 ? (
    <div className={cx('dag-generation-id-list')}>
      {uniqueValues.map((value) => (
        <code key={value}>{value}</code>
      ))}
    </div>
  ) : (
    <Text type="secondary">{empty}</Text>
  )
}

/** 渲染受限文本列表。 */
function BulletList({ values }: { values: string[] }): ReactElement {
  return (
    <ul className={cx('dag-generation-bullet-list')}>
      {values.map((value) => (
        <li key={value}>{value}</li>
      ))}
    </ul>
  )
}

/** 渲染依赖边列表，并标记服务端已截断的情况。 */
function EdgeSection({
  edges,
  title
}: {
  edges?: DagGenerationEdgeList
  title: string
}): ReactElement {
  const items = edges?.items || []
  return (
    <OutputSection icon={<NodeIndexOutlined />} title={title}>
      {items.length > 0 ? (
        <div className={cx('dag-generation-edge-list')}>
          {items.map((edge, index) => (
            <span key={`${edge.from}-${edge.to}-${index}`}>
              <code>{edge.from}</code>
              <RightOutlined />
              <code>{edge.to}</code>
              <small>{edge.type}</small>
            </span>
          ))}
          {edges?.truncated && <Text type="secondary">已显示前 500 条依赖边</Text>}
        </div>
      ) : (
        <Text type="secondary">暂无依赖边</Text>
      )}
    </OutputSection>
  )
}

/** 渲染 DAG 校验信息。 */
function ValidationNotice({
  validation
}: {
  validation: { isValid: boolean; issues: string[] }
}): ReactElement {
  return (
    <div
      className={cx('dag-generation-validation-banner', validation.isValid ? 'valid' : 'invalid')}
    >
      {validation.isValid ? <CheckCircleOutlined /> : <WarningOutlined />}
      <span>
        {validation.isValid ? 'Unit 依赖校验通过' : `发现 ${validation.issues.length} 个问题`}
      </span>
      {validation.issues.length > 0 && <BulletList values={validation.issues} />}
    </div>
  )
}

/** 渲染执行批次。 */
function BatchRow({ batch }: { batch: DagGenerationBatchRecord }): ReactElement {
  return (
    <li>
      <span className={cx('dag-generation-batch-index')}>
        {String(batch.index).padStart(2, '0')}
      </span>
      <span>
        <Text strong>
          {batch.mode === 'parallel'
            ? '并行批次'
            : batch.mode === 'blocked'
              ? '阻塞批次'
              : '串行批次'}
        </Text>
        <Text type="secondary">{batch.taskIds.join('、') || '无任务'}</Text>
      </span>
    </li>
  )
}

/** 渲染单个保存产物。 */
function ArtifactRow({ artifact }: { artifact: DagGenerationArtifactRecord }): ReactElement {
  return (
    <li>
      <FileMarkdownOutlined />
      <span>
        <Text>{artifact.name}</Text>
        <Text type="secondary">
          {artifact.kind === 'internal'
            ? '内部状态已保存'
            : `JSON 产物 · 确认状态：${artifact.confirmationStatus || 'pending'}`}
        </Text>
      </span>
      <CheckCircleOutlined />
    </li>
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

/** 返回 Unit 类型的用户可读名称。 */
function unitKindLabel(kind: string): string {
  return (
    {
      application: '应用',
      frontend: '前端',
      backend: '后端',
      database: '数据库',
      page: '页面',
      app: '应用集成'
    }[kind] ||
    kind ||
    '未知'
  )
}

/** 返回 Unit 状态的用户可读名称。 */
function unitStatusLabel(status: string): string {
  return (
    {
      prepared: '已准备',
      not_prepared: '未准备',
      completed: '已完成',
      running: '执行中'
    }[status] ||
    status ||
    '未知'
  )
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

/** 返回任务状态的中文标签。 */
function taskStatusLabel(status: DagGenerationTaskRecord['status']): string {
  return {
    pending: '待执行',
    running: '执行中',
    completed: '已完成',
    failed: '失败'
  }[status]
}
