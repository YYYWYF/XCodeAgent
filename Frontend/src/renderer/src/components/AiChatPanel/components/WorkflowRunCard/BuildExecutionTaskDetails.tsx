import { CloseCircleOutlined } from '@ant-design/icons'
import { Collapse, Tag, Typography } from 'antd'
import type { WorkflowBuildExecutionTask } from '../../../../typings'
import { cx } from '../../../../utils'
import {
  TaskDisclosureHeader as DisclosureHeader,
  taskDisclosureExpandIcon
} from './TaskDisclosureHeader'
import { BuildTaskFileList } from './BuildTaskFileList'
import { taskFilePresentation } from './buildTaskPresentation'
import {
  acceptanceFailureDetails,
  businessAcceptanceDetails,
  businessAcceptanceStatusColor,
  businessAcceptanceStatusText,
  structuredCheckDescriptions
} from './buildExecutionAcceptance'
import {
  buildToolActivityPlacement,
  executionTaskPlanView,
  taskFailureCategoryText,
  taskFailureReason,
  taskId
} from './buildExecutionPresentation'
import WorkspaceToolActivity from './WorkspaceToolActivity'
import './BuildTaskPlanConfirmationTasks.less'

const { Text, Paragraph } = Typography

/** 复用确认卡文件清单和折叠样式，独立保留执行证据、失败原因与实时工具活动。 */
export function BuildExecutionTaskDetails({
  task,
  dependencyNames,
  expanded
}: {
  task: WorkflowBuildExecutionTask
  dependencyNames: string[]
  expanded: boolean
}): JSX.Element {
  const { files, ranges, supplementaryDescription } = taskFilePresentation(
    executionTaskPlanView(task)
  )
  const engineeringChecks = structuredCheckDescriptions(
    task.engineeringAcceptanceChecks ?? task.engineering_acceptance_checks
  )
  const businessChecks = businessAcceptanceDetails(
    task.businessAcceptanceChecks ?? task.business_acceptance_checks,
    task.businessAcceptanceEvidence ?? task.business_acceptance_evidence
  ).map((item) => ({
    ...item,
    // 只隐藏运行配置提示，保留真实验收状态和其他证据。
    evidence: item.evidence.replaceAll('DAG 业务自检已通过环境变量关闭，本次自动跳过。', '').trim()
  }))
  const engineeringFailures = acceptanceFailureDetails(
    task.acceptanceEvidence ?? task.acceptance_evidence,
    'engineering'
  )
  const businessFailures = acceptanceFailureDetails(
    task.businessAcceptanceEvidence ?? task.business_acceptance_evidence,
    'business'
  )
  const failureCategory = taskFailureCategoryText(task.failure_category)
  const endpoints = task.coveredEndpoints || []
  const failureSummary =
    engineeringFailures[0] ||
    businessFailures[0] ||
    taskFailureReason(task) ||
    '任务执行失败，未提供具体原因'
  const hasDependencies = (task.dependencies || task.dependsOn || []).length > 0

  return (
    <div className={cx('workflow-build-task-details')}>
      {supplementaryDescription && (
        <div className={cx('workflow-build-task-detail-block')}>
          <Text strong>任务补充说明</Text>
          <Paragraph className={cx('workflow-dag-confirmation-instructions')}>
            {supplementaryDescription}
          </Paragraph>
        </div>
      )}
      <Collapse
        bordered={false}
        className={cx('workflow-dag-confirmation-task-disclosures')}
        defaultActiveKey={[]}
        destroyInactivePanel={false}
        expandIconPosition="right"
        expandIcon={taskDisclosureExpandIcon}
      >
        {endpoints.length > 0 && (
          <Collapse.Panel
            key="endpoints"
            header={<DisclosureHeader label="涉及接口" count={endpoints.length} />}
          >
            <div className={cx('workflow-dag-confirmation-task-metadata')}>
              {endpoints.map((endpoint) => (
                <div className={cx('workflow-task-endpoint-row')} key={endpoint.id}>
                  <span
                    className={cx('workflow-task-method')}
                    data-method={endpoint.method.toUpperCase()}
                  >
                    {endpoint.method || 'API'}
                  </span>
                  <div className={cx('workflow-task-endpoint-copy')}>
                    <Text>{endpoint.summary || '未提供接口名称'}</Text>
                    <code>{endpoint.path}</code>
                  </div>
                </div>
              ))}
            </div>
          </Collapse.Panel>
        )}
        <Collapse.Panel
          key="dependencies"
          header={
            <DisclosureHeader
              label="依赖任务"
              count={dependencyNames.length}
              summary={!hasDependencies ? '无' : undefined}
            />
          }
        >
          <TextList
            items={dependencyNames}
            emptyText={hasDependencies ? '依赖任务名称未提供' : '无'}
          />
        </Collapse.Panel>
        <Collapse.Panel
          key="files"
          header={
            <DisclosureHeader label="文件改动" count={files.length} unit="个文件" files={files} />
          }
        >
          {files.length ? (
            <BuildTaskFileList files={files} />
          ) : (
            <Text type="secondary">未声明具体文件</Text>
          )}
        </Collapse.Panel>
        {ranges.length > 0 && (
          <Collapse.Panel
            key="ranges"
            header={<DisclosureHeader label="允许修改范围" count={ranges.length} />}
          >
            <BuildTaskFileList files={ranges} />
          </Collapse.Panel>
        )}
        {engineeringChecks.length > 0 && (
          <Collapse.Panel
            key="engineering"
            header={<DisclosureHeader label="工程检查" count={engineeringChecks.length} />}
          >
            <TextList items={engineeringChecks} />
          </Collapse.Panel>
        )}
        <Collapse.Panel
          key="business"
          header={<DisclosureHeader label="业务检查" count={businessChecks.length} />}
        >
          {businessChecks.length ? (
            <ul className={cx('workflow-build-task-detail-list')}>
              {businessChecks.map((item) => (
                <li className={cx('workflow-build-business-check')} key={item.key}>
                  <div className={cx('workflow-build-business-check-header')}>
                    <Text>{item.title}</Text>
                    <Tag color={businessAcceptanceStatusColor(item.status)}>
                      {businessAcceptanceStatusText(item.status)}
                    </Tag>
                  </div>
                  {item.evidence && (
                    <Paragraph className={cx('workflow-build-check-evidence')}>
                      {item.evidence}
                    </Paragraph>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <Text type="secondary">本任务无独立业务验收标准</Text>
          )}
        </Collapse.Panel>
        <Collapse.Panel
          key="metadata"
          header={
            <DisclosureHeader
              label="执行信息"
              count={1 + (task.status === 'failed' ? 1 : 0)}
              summary={
                task.status === 'failed' ? (
                  <span className={cx('workflow-build-task-error-summary')}>
                    <CloseCircleOutlined aria-hidden="true" />
                    <span className={cx('workflow-build-task-error-text')}>{failureSummary}</span>
                    <span className={cx('workflow-build-task-error-link')}>查看详情</span>
                  </span>
                ) : undefined
              }
            />
          }
        >
          {task.status === 'failed' && (
            <div className={cx('workflow-build-task-detail-block', 'workflow-build-task-failure')}>
              <Text strong>
                <CloseCircleOutlined aria-hidden="true" /> 失败原因
              </Text>
              {engineeringFailures.length > 0 && (
                <FailureList items={engineeringFailures} label="工程验收" />
              )}
              {businessFailures.length > 0 && (
                <FailureList items={businessFailures} label="业务验收" />
              )}
              {!engineeringFailures.length && !businessFailures.length && (
                <Text>{taskFailureReason(task) || '任务执行失败，但后端未返回具体原因。'}</Text>
              )}
              {failureCategory && (
                <Tag className={cx('workflow-build-failure-category')}>{failureCategory}</Tag>
              )}
            </div>
          )}
          <div className={cx('workflow-build-task-detail-block')}>
            <Text type="secondary">任务 ID</Text>
            <Text code>{taskId(task)}</Text>
          </div>
        </Collapse.Panel>
      </Collapse>
      {expanded && buildToolActivityPlacement(task, true) === 'details' && (
        <WorkspaceToolActivity activity={task.activeToolActivity!} />
      )}
    </div>
  )
}

/** 用中性圆点显示名称与说明，不将计划或依赖展示成通过结果。 */
function TextList({
  items,
  emptyText = '暂无'
}: {
  items: string[]
  emptyText?: string
}): JSX.Element {
  return items.length ? (
    <ul className={cx('workflow-build-task-detail-list')}>
      {items.map((item) => (
        <li key={item}>
          <Text>{item}</Text>
        </li>
      ))}
    </ul>
  ) : (
    <Text type="secondary">{emptyText}</Text>
  )
}

/** 按工程和业务分类保留真实失败证据，不从任务状态推测失败内容。 */
function FailureList({ items, label }: { items: string[]; label: string }): JSX.Element {
  return (
    <div className={cx('workflow-build-acceptance-failure-group')}>
      <Text strong>{label}</Text>
      <TextList items={items} />
    </div>
  )
}
