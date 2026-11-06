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

/** 在当前页面开发对话内审阅依赖和正式设计交接，通过主 Workflow 继续原目标。 */
export default function AgentUiPreparationReview({
  preparation,
  disabled,
  onAction
}: Props): ReactElement {
  const [reviewed, setReviewed] = useState(false)
  const plan = preparation?.plan
  const dependency = plan && 'reviewId' in plan ? plan : undefined
  const transfer = plan && 'initialization' in plan ? plan : undefined
  const blocked = preparation?.stage === 'blocked' || transfer?.status === 'blocked' || !plan
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
  const label = dependency
    ? '确认依赖与样式准备'
    : preparation?.stage === 'initialize'
      ? '确认初始化，随后重新审阅设计差异'
      : '确认转入设计并继续页面开发'

  /** 只提交服务端当前计划的身份；不接受客户端源码、路径重选或命令。 */
  const confirm = (): void => {
    if (!reviewed || disabled || blocked || !plan) return
    onAction(
      dependency
        ? {
            action: 'confirm',
            planId: dependency.planId,
            reviewId: dependency.reviewId
          }
        : transfer
          ? {
              action: 'confirm',
              planId: transfer.planId,
              installationPlanId: transfer.installation.planId,
              targetSourceRoot: transfer.target.sourceRoot
            }
          : { action: 'refresh' }
    )
  }

  return (
    <div className={cx('agent-ui-transfer')}>
      <Alert
        type={blocked ? 'warning' : 'info'}
        showIcon
        message="页面开发前的 Agent UI 准备"
        description={
          preparation?.error ||
          (dependency
            ? '请审阅依赖和样式差异。确认后由开发流程执行安装并验证。'
            : preparation?.stage === 'initialize'
              ? '目标尚未初始化。初始化后会再次展示已确认设计与默认文件的差异，再由你确认转入。'
              : '请审阅已确认设计到当前工程的差异。验证成功后继续原页面任务规划。')
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
            {label}
          </Button>
        )}
        <Button disabled={disabled} onClick={() => onAction({ action: 'refresh' })}>
          重新检查当前工程
        </Button>
      </Space>
    </div>
  )
}
