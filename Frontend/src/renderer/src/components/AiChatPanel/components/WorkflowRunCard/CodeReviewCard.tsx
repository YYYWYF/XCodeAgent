import { CheckCircleOutlined, LoadingOutlined, WarningOutlined } from '@ant-design/icons'
import { Button, Empty, Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { WorkflowCodeReviewRepair, WorkflowCodeReviewResult } from '../../../../typings'
import { cx } from '../../../../utils'

const { Text } = Typography

type Props = {
  result?: WorkflowCodeReviewResult
  repair?: WorkflowCodeReviewRepair
  running?: boolean
  canRepair?: boolean
  onRepairAll?: () => void
}

const severityLabels: Record<string, string> = {
  critical: '严重',
  high: '高',
  medium: '中',
  low: '低'
}

const reviewBuildCheckDefinitions = [
  { id: 'frontend_install', name: '前端依赖安装检查' },
  { id: 'frontend_build', name: '前端构建检查' },
  { id: 'backend_build', name: '后端构建检查' }
] as const

/** 将内部严重级别映射成审查卡片中的可读文案。 */
function severityLabel(value?: string): string {
  return severityLabels[String(value || '').toLowerCase()] || '一般'
}

/** 将审查问题路径格式化为相对工作区展示文本，避免界面暴露绝对路径。 */
function displayFile(issue: NonNullable<WorkflowCodeReviewResult['issues']>[number]): string {
  const file = String(issue.file || '').trim()
  const line = Number.isFinite(issue.line) && Number(issue.line) > 0 ? `:${issue.line}` : ''
  return `${file || '未提供文件位置'}${line}`
}

/** 渲染代码审查加载态、目标摘要、问题列表及受控的一键修复状态。 */
export default function CodeReviewCard({
  result,
  repair,
  running = false,
  canRepair = false,
  onRepairAll
}: Props): ReactElement {
  const targets = Array.isArray(result?.targets) ? result.targets : []
  const issues = Array.isArray(result?.issues) ? result.issues : []
  const issueCount = Number.isFinite(result?.issueCount)
    ? Number(result?.issueCount)
    : issues.length
  const repairStatus = String(
    repair?.status || (issues.length > 0 ? 'awaiting_user' : 'not_required')
  )
  const repairing = repairStatus === 'repairing'
  const building = repairStatus === 'building'
  const repairCompleted = repairStatus === 'completed'
  const repairFailed = repairStatus === 'failed'
  const scanRunning = !result || (running && repairStatus === 'not_required')
  const buildChecks = reviewBuildCheckDefinitions.map((definition) => {
    const current = repair?.buildChecks?.find((check) => check.id === definition.id)
    return (
      current || {
        id: definition.id,
        name: definition.name,
        status: repairCompleted || repairFailed ? 'skipped' : 'running'
      }
    )
  })

  if (repairing) {
    return (
      <section aria-live="polite" className={cx('workflow-code-review-card', 'running')}>
        <span className={cx('workflow-code-review-icon')} aria-hidden="true">
          <LoadingOutlined spin />
        </span>
        <div className={cx('workflow-code-review-copy')}>
          <Text strong>正在修复审查的问题，请稍候…</Text>
          <Text type="secondary">正在处理扫描出的 {issueCount} 个问题，不会重复执行代码扫描</Text>
        </div>
      </section>
    )
  }

  if (scanRunning) {
    return (
      <section aria-live="polite" className={cx('workflow-code-review-card', 'running')}>
        <span className={cx('workflow-code-review-icon')} aria-hidden="true">
          <LoadingOutlined spin />
        </span>
        <div className={cx('workflow-code-review-copy')}>
          <Text strong>正在审查前后端代码，请稍候…</Text>
          <Text type="secondary">仅扫描 frontend/src 与 backend/src/main/java</Text>
        </div>
      </section>
    )
  }

  return (
    <section aria-live="polite" className={cx('workflow-code-review-card', 'completed')}>
      <div className={cx('workflow-code-review-header')}>
        <span className={cx('workflow-code-review-icon')} aria-hidden="true">
          <CheckCircleOutlined />
        </span>
        <div className={cx('workflow-code-review-copy')}>
          <Text strong>
            {building
              ? '代码修复完成，正在执行构建检查'
              : repairCompleted
                ? '代码修复完成'
                : '代码审查完成'}
          </Text>
          <Text type="secondary">{result.summary || '前后端代码扫描已完成。'}</Text>
        </div>
        <Tag>{issueCount} 个问题</Tag>
      </div>

      <div className={cx('workflow-code-review-targets')}>
        {targets.map((target, index) => (
          <div
            className={cx('workflow-code-review-target')}
            key={`${target.side || 'target'}-${index}`}
          >
            <div className={cx('workflow-code-review-target-main')}>
              <Text strong>{target.root || '未指定扫描目录'}</Text>
              <Text type="secondary">
                {target.status === 'skipped'
                  ? '已跳过'
                  : `已扫描 ${Number(target.scannedFileCount || 0)} 个文件`}
              </Text>
            </div>
            {target.warning ? (
              <Text className={cx('workflow-code-review-warning')} type="warning">
                <WarningOutlined /> {target.warning}
              </Text>
            ) : null}
          </div>
        ))}
      </div>

      {issues.length > 0 ? (
        <div className={cx('workflow-code-review-issues')}>
          <div className={cx('workflow-code-review-section-title')}>
            <Text strong>发现的问题</Text>
            {result.truncated ? <Tag color="orange">已展示前 100 条</Tag> : null}
          </div>
          <div className={cx('workflow-code-review-issue-list')}>
            {issues.map((issue, index) => (
              <div
                className={cx('workflow-code-review-issue')}
                key={issue.id || `${issue.file}-${index}`}
              >
                <div className={cx('workflow-code-review-issue-header')}>
                  <Tag
                    color={
                      issue.severity === 'critical' || issue.severity === 'high' ? 'red' : 'gold'
                    }
                  >
                    {severityLabel(issue.severity)}
                  </Tag>
                  {issue.ruleId ? <Tag>{issue.ruleId}</Tag> : null}
                  <Text strong>{issue.title || '未命名问题'}</Text>
                </div>
                <Text type="secondary">{issue.summary || '审查 Agent 未提供问题说明。'}</Text>
                <Text code className={cx('workflow-code-review-file')}>
                  {displayFile(issue)}
                </Text>
              </div>
            ))}
          </div>
          {repair?.summary ? (
            <Text
              className={cx(
                'workflow-code-review-repair-summary',
                repairFailed ? 'failed' : undefined
              )}
              type={repairFailed ? 'danger' : 'secondary'}
            >
              {repair.summary}
            </Text>
          ) : null}
          {building || repairCompleted || repairFailed || repair?.buildChecks?.length ? (
            <div className={cx('workflow-code-review-build-checks')} aria-live="polite">
              {buildChecks.map((check, index) => (
                <div
                  className={cx('workflow-code-review-build-check')}
                  key={`${check.id || check.name}-${index}`}
                >
                  <span
                    className={cx(
                      'workflow-code-review-build-check-status',
                      check.status || 'running'
                    )}
                    aria-hidden="true"
                  />
                  <Text>{check.name || check.id || '构建检查'}</Text>
                  <Text type="secondary">
                    {check.status === 'passed'
                      ? '已通过'
                      : check.status === 'failed'
                        ? '未通过'
                        : check.status === 'skipped'
                          ? '已跳过'
                          : '检查中'}
                  </Text>
                </div>
              ))}
            </div>
          ) : null}
          <div className={cx('workflow-code-review-actions')}>
            {repairing || building ? (
              <Text type="secondary">
                {repairing
                  ? `正在修复第 ${repair?.iteration || 1}/${repair?.maxIterations || 3} 轮…`
                  : '正在执行前后端构建检查…'}
              </Text>
            ) : repairCompleted ? (
              <Text type="success">修复已执行，前后端构建检查通过</Text>
            ) : repairFailed ? (
              <Text type="danger">修复失败，项目未启动</Text>
            ) : (
              <Button
                type="primary"
                loading={repairStatus === 'repairing' || repairStatus === 'building'}
                disabled={!canRepair}
                onClick={onRepairAll}
              >
                {result.truncated
                  ? `修复当前 ${Math.min(issueCount, 100)} 项`
                  : `一键修复全部 ${issueCount} 项`}
              </Button>
            )}
          </div>
        </div>
      ) : (
        <div className={cx('workflow-code-review-empty')}>
          <Empty image={<CheckCircleOutlined />} description="未发现需要处理的问题" />
        </div>
      )}
    </section>
  )
}
