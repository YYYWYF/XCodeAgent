import { Alert, Button, Checkbox, Collapse, Space, Typography } from 'antd'
import { useState, type ReactElement } from 'react'
import type {
  WorkflowAgentUiPreparation,
  WorkflowAgentUiPreparationAction
} from '../../../../typings/workflow'
import { cx } from '../../../../utils'
import '../../../AgentUiTransfer/index.less'

type Props = {
  preparation?: WorkflowAgentUiPreparation
  disabled: boolean
  onAction: (answer: WorkflowAgentUiPreparationAction) => void
}

/** 常规准备由主流程自动执行，仅展示生产依赖确认和需处理的异常。 */
export default function AgentUiPreparationReview({
  preparation,
  disabled,
  onAction
}: Props): ReactElement {
  const [reviewed, setReviewed] = useState(false)
  const plan = preparation?.plan
  const dependency = plan && 'reviewId' in plan ? plan : undefined
  const transfer = plan && 'initialization' in plan ? plan : undefined
  const blocked = preparation?.stage !== 'dependency' || !dependency
  const changes =
    dependency?.changes.map((change) => ({
      path: change.path,
      operation: change.beforeSha256 ? '修改' : '新增',
      content: change.after
    })) ||
    transfer?.changes
      .filter((change) => change.operation !== 'unchanged')
      .map((change) => ({
        path: change.targetPath,
        operation: change.operation,
        content: change.diff || change.after
      })) ||
    []

  /** 只提交服务端当前计划的身份；不接受客户端源码、路径重选或命令。 */
  const confirm = (): void => {
    if (!reviewed || disabled || blocked || !dependency) return
    onAction({ action: 'confirm', planId: dependency.planId, reviewId: dependency.reviewId })
  }

  return (
    <div className={cx('agent-ui-transfer')}>
      <Alert
        type={blocked ? 'warning' : 'info'}
        showIcon
        message={blocked ? '智能体页面准备需要处理' : '确认新增或变更的生产依赖'}
        description={
          preparation?.error ||
          (blocked
            ? '请先处理提示的问题，已有文件会保留。重新检查通过后将自动继续准备和页面开发。'
            : '工程需要新增或变更生产依赖，请审阅具体差异。确认后将自动安装、初始化、转入已确认设计并验证。')
        }
      />
      {dependency && (
        <Typography.Paragraph>
          {dependency.manager}@{dependency.managerVersion}；
          {dependency.packages.map((item) => `${item.name}@${item.version}`).join('、')}；
          {dependency.sourceDependencies.map((item) => `${item.name}@${item.version}`).join('、')}
        </Typography.Paragraph>
      )}
      {transfer?.blockers.map((reason) => (
        <Alert key={reason} type="warning" message={reason} />
      ))}
      {transfer?.conflicts.map((path) => (
        <Alert key={path} type="warning" message={`保留现有修改：${path}`} />
      ))}
      {transfer && (
        <Typography.Paragraph type="secondary">
          {transfer.hostIntegration.reason}
        </Typography.Paragraph>
      )}
      {changes.length > 0 && (
        <Collapse>
          {changes.map((change) => (
            <Collapse.Panel key={change.path} header={`${change.path} · ${change.operation}`}>
              <pre style={{ maxHeight: 320, overflow: 'auto', whiteSpace: 'pre-wrap' }}>
                {change.content}
              </pre>
            </Collapse.Panel>
          ))}
          <Collapse.Panel key="validation" header="验证与失败恢复">
            <Typography.Paragraph>
              {dependency ? dependency.checks.join('、') : transfer?.validation.join('；')}
            </Typography.Paragraph>
            <Typography.Paragraph>{plan?.recovery}</Typography.Paragraph>
          </Collapse.Panel>
        </Collapse>
      )}
      {!blocked && (
        <Checkbox
          checked={reviewed}
          disabled={disabled}
          onChange={(event) => setReviewed(event.target.checked)}
        >
          我已审阅本轮差异、验证及恢复范围
        </Checkbox>
      )}
      <Space wrap>
        {!blocked && (
          <Button type="primary" disabled={disabled || !reviewed} onClick={confirm}>
            确认依赖变更并继续
          </Button>
        )}
        <Button disabled={disabled} onClick={() => onAction({ action: 'refresh' })}>
          重新检查当前工程
        </Button>
      </Space>
    </div>
  )
}
