import {
  ApiOutlined,
  BranchesOutlined,
  FileTextOutlined,
  FolderOutlined,
  UnorderedListOutlined
} from '@ant-design/icons'
import { Collapse, Typography } from 'antd'
import type {
  WorkflowBuildTargetReview,
  WorkflowBuildTargetReviewEndpoint,
  WorkflowBuildTaskPlanRetainedSummary,
  WorkflowBuildTaskPlanTask
} from '../../../../typings'
import { cx } from '../../../../utils'
import { BuildTaskFileList } from './BuildTaskFileList'
import { taskDisplayTitle, taskFilePresentation } from './buildTaskPresentation'

/** 将目标与修改范围合并展示，并仅在页面确有关联接口时追加接口契约。 */
export function TargetScopeReview({ review }: { review: WorkflowBuildTargetReview }): JSX.Element {
  const { target, relatedEndpoints } = review
  const isPage = target.type === 'page'
  const isEndpoint = target.type === 'endpoint'

  return (
    <section className={cx('workflow-dag-confirmation-target')}>
      <div className={cx('workflow-dag-confirmation-section-heading')}>
        <span>
          {isEndpoint ? (
            <ApiOutlined aria-hidden="true" />
          ) : (
            <FileTextOutlined aria-hidden="true" />
          )}
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

/** 展示与本轮无关的累计任务状态摘要。 */
export function RetainedTaskSummary({
  retainedSummary
}: {
  retainedSummary?: WorkflowBuildTaskPlanRetainedSummary
}): JSX.Element | null {
  if (!retainedSummary?.total) return null
  return (
    <aside className={cx('workflow-dag-confirmation-existing-summary')}>
      <Typography.Text type="secondary">
        另有 {retainedSummary.total} 个累计任务不在本轮确认范围内
        {retainedSummary.completed ? `，其中 ${retainedSummary.completed} 个已完成` : ''}
      </Typography.Text>
    </aside>
  )
}

/** 折叠态仅展示任务序号与业务名称，将细节集中到任务正文。 */
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
        <span className={cx('workflow-dag-confirmation-task-title-line')}>
          <Typography.Text strong>
            {taskDisplayTitle(task) || `开发任务 ${index + 1}`}
          </Typography.Text>
        </span>
      </span>
    </div>
  )
}

/** 将涉及接口、依赖、文件和待验收内容独立折叠并保留内部展开状态。 */
export function TaskDetails({
  task,
  dependencyNames = []
}: {
  task: WorkflowBuildTaskPlanTask
  dependencyNames?: string[]
}): JSX.Element {
  const { files, ranges, supplementaryDescription } = taskFilePresentation(task)
  const businessChecks = acceptanceTexts(task.business_acceptance_checks)
  const coveredEndpoints = task.coveredEndpoints || []
  return (
    <div className={cx('workflow-dag-confirmation-task-details')}>
      {supplementaryDescription ? (
        <div className={cx('workflow-dag-confirmation-task-detail-block')}>
          <Typography.Text className={cx('workflow-dag-confirmation-subsection-title')} strong>
            任务补充说明
          </Typography.Text>
          <Typography.Paragraph className={cx('workflow-dag-confirmation-instructions')}>
            {supplementaryDescription}
          </Typography.Paragraph>
        </div>
      ) : null}
      <Collapse
        bordered={false}
        className={cx('workflow-dag-confirmation-task-disclosures')}
        defaultActiveKey={[]}
        destroyInactivePanel={false}
        expandIconPosition="right"
      >
        {coveredEndpoints.length > 0 ? (
          <Collapse.Panel
            header={<DisclosureHeader count={coveredEndpoints.length} label="涉及接口" />}
            key="endpoints"
          >
            <div className={cx('workflow-dag-confirmation-task-metadata')}>
              {coveredEndpoints.map((endpoint) => (
                <div className={cx('workflow-task-endpoint-row')} key={endpoint.id}>
                  <span
                    className={cx('workflow-task-method')}
                    data-method={endpoint.method.toUpperCase()}
                  >
                    {endpoint.method || 'API'}
                  </span>
                  <div className={cx('workflow-task-endpoint-copy')}>
                    <Typography.Text>{endpoint.summary || '未提供接口名称'}</Typography.Text>
                    <code>{endpoint.path}</code>
                  </div>
                </div>
              ))}
            </div>
          </Collapse.Panel>
        ) : null}
        <Collapse.Panel
          header={<DisclosureHeader count={dependencyNames.length} label="依赖任务" />}
          key="dependencies"
        >
          <AcceptanceList items={dependencyNames} emptyText="无可展示的依赖任务" />
        </Collapse.Panel>
        <Collapse.Panel
          header={<DisclosureHeader count={files.length} label="文件改动" unit="个文件" />}
          key="files"
        >
          {files.length > 0 ? (
            <BuildTaskFileList files={files} />
          ) : (
            <Typography.Text type="secondary">未声明具体文件</Typography.Text>
          )}
        </Collapse.Panel>
        {ranges.length > 0 ? (
          <Collapse.Panel
            header={<DisclosureHeader count={ranges.length} label="允许修改范围" />}
            key="scope"
          >
            <BuildTaskFileList files={ranges} />
          </Collapse.Panel>
        ) : null}
        <Collapse.Panel
          header={<DisclosureHeader count={businessChecks.length} label="待验收" />}
          key="business-acceptance"
        >
          <AcceptanceList items={businessChecks} emptyText="本任务无独立业务验收标准" />
        </Collapse.Panel>
      </Collapse>
    </div>
  )
}

/** 展示二级折叠项名称与内容数量，帮助用户判断是否需要展开。 */
function DisclosureHeader({
  count,
  label,
  unit = '项'
}: {
  count: number
  label: string
  unit?: string
}): JSX.Element {
  const icons = {
    涉及接口: <ApiOutlined />,
    依赖任务: <BranchesOutlined />,
    文件改动: <FileTextOutlined />,
    允许修改范围: <FolderOutlined />,
    待验收: <UnorderedListOutlined />
  }
  return (
    <span className={cx('workflow-dag-confirmation-disclosure-header')}>
      <span className={cx('workflow-dag-confirmation-disclosure-label')}>
        <span aria-hidden="true">{icons[label as keyof typeof icons]}</span>
        <Typography.Text>{label}</Typography.Text>
      </span>
      <small>
        {count} {unit}
      </small>
    </span>
  )
}

/** 以紧凑清单展示用户可核对的业务验收文本。 */
function AcceptanceList({
  emptyText,
  items,
  title
}: {
  emptyText?: string
  items: string[]
  title?: string
}): JSX.Element {
  return (
    <div className={cx('workflow-dag-confirmation-acceptance')}>
      {title ? (
        <Typography.Text className={cx('workflow-dag-confirmation-acceptance-title')}>
          {title}
        </Typography.Text>
      ) : null}
      {items.length > 0 ? (
        <ul>
          {items.map((item) => (
            <li key={item}>
              <span className={cx('workflow-dag-confirmation-acceptance-dot')} aria-hidden="true">
                •
              </span>
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
