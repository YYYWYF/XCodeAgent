import { ApiOutlined, CheckCircleOutlined, FileTextOutlined } from '@ant-design/icons'
import { Typography } from 'antd'
import type {
  WorkflowBuildTargetReview,
  WorkflowBuildTargetReviewEndpoint,
  WorkflowBuildTaskPlanPrerequisite,
  WorkflowBuildTaskPlanRetainedSummary,
  WorkflowBuildTaskPlanTask
} from '../../../../typings'
import { cx } from '../../../../utils'

/** 将目标与修改范围合并展示，并仅在页面确有关联接口时追加接口契约。 */
export function TargetScopeReview({
  review
}: {
  review: WorkflowBuildTargetReview
}): JSX.Element {
  const { target, relatedEndpoints } = review
  const isPage = target.type === 'page'
  const isEndpoint = target.type === 'endpoint'

  return (
    <section className={cx('workflow-dag-confirmation-target')}>
      <div className={cx('workflow-dag-confirmation-section-heading')}>
        <span>
          {isEndpoint ? <ApiOutlined aria-hidden="true" /> : <FileTextOutlined aria-hidden="true" />}
          <Typography.Text strong>本次开发目标与范围</Typography.Text>
        </span>
      </div>
      <div className={cx('workflow-dag-confirmation-target-card')}>
        <div className={cx('workflow-dag-confirmation-target-heading')}>
          <span className={cx('workflow-dag-confirmation-target-type')}>
            {targetTypeLabel(target.type)}
          </span>
          <Typography.Text strong>{target.label}</Typography.Text>
          {target.path ? <code>{target.path}</code> : null}
        </div>
        {target.description ? (
          <Typography.Paragraph className={cx('workflow-dag-confirmation-target-description')}>
            {target.description}
          </Typography.Paragraph>
        ) : null}

        {isPage ? (
          <AcceptanceList
            emptyText="该页面未提供独立的业务验收标准"
            items={target.acceptanceCriteria || []}
            title="页面业务验收标准"
          />
        ) : null}
        {isEndpoint ? (
          <EndpointContract endpoint={target as WorkflowBuildTargetReviewEndpoint} nested={false} />
        ) : null}

        {isPage && relatedEndpoints && relatedEndpoints.length > 0 ? (
          <div className={cx('workflow-dag-confirmation-related-endpoints')}>
            <Typography.Text className={cx('workflow-dag-confirmation-subsection-title')} strong>
              该页面关联接口
            </Typography.Text>
            <div className={cx('workflow-dag-confirmation-endpoint-list')}>
              {relatedEndpoints.map((endpoint) => (
                <EndpointContract endpoint={endpoint} key={endpoint.id} nested />
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </section>
  )
}

/** 展示接口详细设计中的请求、响应、鉴权和错误约束。 */
function EndpointContract({
  endpoint,
  nested
}: {
  endpoint: WorkflowBuildTargetReviewEndpoint
  nested: boolean
}): JSX.Element {
  const details = endpointContractDetails(endpoint)
  return (
    <article
      className={cx(
        'workflow-dag-confirmation-endpoint',
        nested && 'workflow-dag-confirmation-endpoint-nested'
      )}
    >
      <div className={cx('workflow-dag-confirmation-endpoint-heading')}>
        <span className={cx('workflow-dag-confirmation-method')}>{endpoint.method || 'API'}</span>
        <code>{endpoint.path || '未声明路径'}</code>
        {endpoint.summary ? <Typography.Text>{endpoint.summary}</Typography.Text> : null}
      </div>
      <dl className={cx('workflow-dag-confirmation-contract-grid')}>
        {details.map((detail) => (
          <div key={detail.label}>
            <dt>{detail.label}</dt>
            <dd>{detail.value}</dd>
          </div>
        ))}
      </dl>
    </article>
  )
}

/** 展示不属于本次修改范围、但会被当前任务复用的既有能力和历史汇总。 */
export function ReusedCapabilitySummary({
  prerequisites,
  retainedSummary
}: {
  prerequisites: WorkflowBuildTaskPlanPrerequisite[]
  retainedSummary?: WorkflowBuildTaskPlanRetainedSummary
}): JSX.Element | null {
  if (prerequisites.length === 0 && !retainedSummary?.total) return null
  return (
    <aside className={cx('workflow-dag-confirmation-existing-summary')}>
      {prerequisites.length > 0 ? (
        <div>
          <Typography.Text strong>已有前置能力</Typography.Text>
          <span className={cx('workflow-dag-confirmation-prerequisites')}>
            {prerequisites.map((item) => (
              <span key={item.id}>
                {item.title}
                <small>{taskStatusLabel(item.status)}</small>
              </span>
            ))}
          </span>
        </div>
      ) : null}
      {retainedSummary?.total ? (
        <Typography.Text type="secondary">
          另有 {retainedSummary.total} 个累计任务不在本次修改范围内
          {retainedSummary.completed ? `，其中 ${retainedSummary.completed} 个已完成` : ''}
        </Typography.Text>
      ) : null}
    </aside>
  )
}

/** 展示折叠态任务摘要，不暴露任务 ID、Unit 或依赖边。 */
export function TaskHeader({
  index,
  task
}: {
  index: number
  task: WorkflowBuildTaskPlanTask
}): JSX.Element {
  return (
    <div className={cx('workflow-dag-confirmation-task-header')}>
      <span className={cx('workflow-dag-confirmation-task-index')}>{index + 1}</span>
      <span className={cx('workflow-dag-confirmation-task-copy')}>
        <Typography.Text strong>{task.title || `开发任务 ${index + 1}`}</Typography.Text>
        {task.description ? <Typography.Text type="secondary">{task.description}</Typography.Text> : null}
      </span>
    </div>
  )
}

/** 展示单任务的修改边界与可核对验收标准。 */
export function TaskDetails({ task }: { task: WorkflowBuildTaskPlanTask }): JSX.Element {
  const paths = taskPaths(task)
  const businessChecks = acceptanceTexts(task.business_acceptance_checks)
  const engineeringChecks = acceptanceTexts(task.acceptance_checks)
  return (
    <div className={cx('workflow-dag-confirmation-task-details')}>
      <div className={cx('workflow-dag-confirmation-task-detail-block')}>
        <Typography.Text className={cx('workflow-dag-confirmation-subsection-title')} strong>
          修改范围
        </Typography.Text>
        {paths.length > 0 ? (
          <div className={cx('workflow-dag-confirmation-paths')}>
            {paths.map((path) => (
              <code key={path}>{path}</code>
            ))}
          </div>
        ) : (
          <Typography.Text type="secondary">未声明具体文件路径</Typography.Text>
        )}
      </div>
      <div className={cx('workflow-dag-confirmation-task-detail-block')}>
        <Typography.Text className={cx('workflow-dag-confirmation-subsection-title')} strong>
          验收标准
        </Typography.Text>
        {businessChecks.length > 0 ? (
          <AcceptanceList items={businessChecks} title="业务验收" />
        ) : null}
        {engineeringChecks.length > 0 ? (
          <AcceptanceList items={engineeringChecks} title="工程验收" />
        ) : null}
        {businessChecks.length === 0 && engineeringChecks.length === 0 ? (
          <Typography.Text type="secondary">该任务未提供独立验收标准</Typography.Text>
        ) : null}
      </div>
    </div>
  )
}

/** 以紧凑清单展示业务或工程验收文本。 */
function AcceptanceList({
  emptyText,
  items,
  title
}: {
  emptyText?: string
  items: string[]
  title: string
}): JSX.Element {
  return (
    <div className={cx('workflow-dag-confirmation-acceptance')}>
      <Typography.Text className={cx('workflow-dag-confirmation-acceptance-title')}>
        {title}
      </Typography.Text>
      {items.length > 0 ? (
        <ul>
          {items.map((item) => (
            <li key={item}>
              <CheckCircleOutlined aria-hidden="true" />
              <span>{item}</span>
            </li>
          ))}
        </ul>
      ) : (
        <Typography.Text type="secondary">{emptyText || '暂无'}</Typography.Text>
      )}
    </div>
  )
}

/** 将目标类型映射为用户可理解的名称。 */
function targetTypeLabel(type: string): string {
  if (type === 'page') return '页面'
  if (type === 'endpoint') return '接口'
  if (type === 'data_source') return '数据源'
  return '应用'
}

/** 从接口契约生成只读详情行，空字段也明确显示为无，避免误判为漏加载。 */
function endpointContractDetails(
  endpoint: WorkflowBuildTargetReviewEndpoint
): Array<{ label: string; value: string }> {
  const parameters = endpoint.parameters
    .map((item) => {
      const name = String(item.name || '').trim()
      if (!name) return ''
      const location = String(item.in || '').trim()
      return `${name}${location ? `（${location}${item.required ? '，必填' : ''}）` : ''}`
    })
    .filter(Boolean)
  const authenticationRequired = endpoint.authentication.required
  return [
    { label: '请求参数', value: parameters.join('、') || '无' },
    { label: '请求体', value: endpoint.requestSchemaRef || '无' },
    { label: '返回结构', value: endpoint.responseSchemaRef || '未声明' },
    {
      label: '鉴权',
      value:
        authenticationRequired === true
          ? '需要鉴权'
          : authenticationRequired === false
            ? '无需鉴权'
            : '未声明'
    },
    { label: '错误码', value: endpoint.errorCodes.join('、') || '无' }
  ]
}

/** 收集任务声明的文件边界和交付物路径并去重。 */
function taskPaths(task: WorkflowBuildTaskPlanTask): string[] {
  const paths = [
    ...(task.target_files || []),
    ...(task.allowed_paths || []),
    ...(task.change_scope || []).flatMap((item) =>
      typeof item.path === 'string' ? [item.path] : []
    ),
    ...(task.deliverables || []).flatMap((item) =>
      Array.isArray(item.paths)
        ? item.paths.flatMap((path) => (typeof path === 'string' ? [path] : []))
        : []
    )
  ]
  return [...new Set(paths.map((path) => path.trim()).filter(Boolean))]
}

/** 从确定性验收对象中提取用户可读说明并去重。 */
function acceptanceTexts(checks?: Array<Record<string, unknown>>): string[] {
  const values = (checks || []).flatMap((check) => {
    for (const key of ['description', 'summary', 'title', 'label']) {
      const value = check[key]
      if (typeof value === 'string' && value.trim()) return [value.trim()]
    }
    return []
  })
  return [...new Set(values)]
}

/** 将内部任务状态压缩为复用能力旁的简短状态。 */
function taskStatusLabel(status?: string): string {
  if (status === 'completed' || status === 'already_satisfied') return '已具备'
  if (status === 'failed') return '需处理'
  if (status === 'running') return '进行中'
  return '待执行'
}
