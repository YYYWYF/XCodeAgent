import {
  CheckCircleFilled,
  ExclamationCircleFilled,
  FileTextOutlined,
  LoadingOutlined,
  SafetyCertificateOutlined
} from '@ant-design/icons'
import { Button, Tag, Typography } from 'antd'
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
  high: '高风险',
  medium: '中风险',
  low: '低风险'
}

/** 将内部严重级别映射成审查卡片中的可读文案。 */
function severityLabel(value?: string): string {
  return severityLabels[String(value || '').toLowerCase()] || '一般'
}

/** 返回问题严重级别对应的主题样式类。 */
function severityClassName(value?: string): string {
  const severity = String(value || '').toLowerCase()
  return ['critical', 'high', 'medium', 'low'].includes(severity) ? severity : 'low'
}

/** 将前后端内部标识转换为问题卡片标签。 */
function sideLabel(value?: string): string {
  return value === 'frontend' ? '前端' : value === 'backend' ? '后端' : '项目'
}

/** 将审查问题路径格式化为相对工作区展示文本，避免界面暴露绝对路径。 */
function displayFile(issue: NonNullable<WorkflowCodeReviewResult['issues']>[number]): string {
  const file = String(issue.file || '').trim()
  const line = Number.isFinite(issue.line) && Number(issue.line) > 0 ? `:${issue.line}` : ''
  return `${file || '未提供文件位置'}${line}`
}

/** 渲染精简审查状态、主题化问题列表及受控的一键修复状态。 */
export default function CodeReviewCard({
  result,
  repair,
  running = false,
  canRepair = false,
  onRepairAll
}: Props): ReactElement {
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
  if (repairing) {
    return (
      <section aria-live="polite" className={cx('workflow-code-review-card', 'running')}>
        <div className={cx('workflow-code-review-loading-line')}>
          <span className={cx('workflow-code-review-loading-icon')} aria-hidden="true">
            <LoadingOutlined spin />
          </span>
          <Text strong>正在修复审查问题，请稍候…</Text>
        </div>
        <Text className={cx('workflow-code-review-loading-hint')} type="secondary">
          正在处理 {issueCount} 个问题，不会重复执行代码扫描
        </Text>
      </section>
    )
  }

  if (scanRunning) {
    return (
      <section aria-live="polite" className={cx('workflow-code-review-card', 'running')}>
        <div className={cx('workflow-code-review-loading-line')}>
          <span className={cx('workflow-code-review-loading-icon')} aria-hidden="true">
            <LoadingOutlined spin />
          </span>
          <Text strong>正在审查前后端代码，请稍候…</Text>
        </div>
        <Text className={cx('workflow-code-review-loading-hint')} type="secondary">
          扫描完成后将在右侧生成完整审查报告
        </Text>
      </section>
    )
  }

  return (
    <section aria-live="polite" className={cx('workflow-code-review-card', 'completed')}>
      <div className={cx('workflow-code-review-completion')}>
        <span
          className={cx('workflow-code-review-icon', issues.length > 0 && 'warning')}
          aria-hidden="true"
        >
          {issues.length > 0 ? <SafetyCertificateOutlined /> : <CheckCircleFilled />}
        </span>
        <div className={cx('workflow-code-review-copy')}>
          <Text strong>
            {building
              ? '代码修复完成，正在执行构建检查'
              : repairCompleted
                ? '代码修复完成'
                : '代码审查已完成'}
          </Text>
          <Text type="secondary">
            {issues.length > 0
              ? `发现 ${issueCount} 个问题，完整结果见右侧审查报告`
              : '完整结果见右侧审查报告'}
          </Text>
        </div>
      </div>

      {issues.length > 0 ? (
        <div className={cx('workflow-code-review-issues')}>
          <div className={cx('workflow-code-review-section-title')}>
            <div>
              <ExclamationCircleFilled aria-hidden="true" />
              <Text strong>待处理问题</Text>
            </div>
            <Tag>{issueCount} 项</Tag>
            {result?.truncated ? (
              <Tag className={cx('workflow-code-review-truncated')}>前 100 条</Tag>
            ) : null}
          </div>
          <div className={cx('workflow-code-review-issue-list')}>
            {issues.map((issue, index) => {
              const severity = severityClassName(issue.severity)
              return (
                <article
                  className={cx('workflow-code-review-issue', severity)}
                  key={issue.id || `${issue.file}-${index}`}
                >
                  <span className={cx('workflow-code-review-severity-bar')} aria-hidden="true" />
                  <div className={cx('workflow-code-review-issue-body')}>
                    <div className={cx('workflow-code-review-issue-meta')}>
                      <Tag className={cx('workflow-code-review-side-tag')}>
                        {sideLabel(issue.side)}
                      </Tag>
                      <Tag className={cx('workflow-code-review-severity-tag', severity)}>
                        {severityLabel(issue.severity)}
                      </Tag>
                      {issue.ruleId ? (
                        <Text className={cx('workflow-code-review-rule')}>{issue.ruleId}</Text>
                      ) : null}
                    </div>
                    <Text className={cx('workflow-code-review-issue-title')} strong>
                      {issue.title || '未命名问题'}
                    </Text>
                    <Text className={cx('workflow-code-review-issue-summary')} type="secondary">
                      {issue.summary || '审查 Agent 未提供问题说明。'}
                    </Text>
                    <div className={cx('workflow-code-review-file')}>
                      <FileTextOutlined aria-hidden="true" />
                      <Text>{displayFile(issue)}</Text>
                    </div>
                  </div>
                </article>
              )
            })}
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
          {building ? (
            <div
              className={cx('workflow-code-review-build-progress')}
              aria-live="polite"
              role="status"
            >
              <LoadingOutlined spin aria-hidden="true" />
              <Text strong>正在执行前后端构建检查</Text>
            </div>
          ) : null}
          {!building ? (
            <div className={cx('workflow-code-review-actions')}>
              {repairCompleted ? (
                <Text type="success">修复已执行，前后端构建检查通过</Text>
              ) : repairFailed ? (
                <Text type="danger">修复失败，项目未启动</Text>
              ) : (
                <>
                  <Text type="secondary">
                    第 {repair?.iteration || 0}/{repair?.maxIterations || 3} 轮
                  </Text>
                  <Button type="primary" disabled={!canRepair} onClick={onRepairAll}>
                    {result?.truncated
                      ? `修复当前 ${Math.min(issueCount, 100)} 项`
                      : `一键修复全部 ${issueCount} 项`}
                  </Button>
                </>
              )}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}
