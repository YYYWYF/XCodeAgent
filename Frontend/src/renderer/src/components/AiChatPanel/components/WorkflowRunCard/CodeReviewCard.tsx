import {
  CheckCircleOutlined,
  LoadingOutlined,
  WarningOutlined
} from '@ant-design/icons'
import { Button, Empty, Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { WorkflowCodeReviewResult } from '../../../../typings'
import { cx } from '../../../../utils'

const { Text } = Typography

type Props = {
  result?: WorkflowCodeReviewResult
  running?: boolean
}

const severityLabels: Record<string, string> = {
  critical: '严重',
  high: '高',
  medium: '中',
  low: '低'
}

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

/** 渲染只读代码审查加载态、目标摘要和问题列表；按钮本期保持无副作用。 */
export default function CodeReviewCard({ result, running = false }: Props): ReactElement {
  const targets = Array.isArray(result?.targets) ? result.targets : []
  const issues = Array.isArray(result?.issues) ? result.issues : []
  const issueCount = Number.isFinite(result?.issueCount)
    ? Number(result?.issueCount)
    : issues.length

  if (running || !result) {
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
          <Text strong>代码审查完成</Text>
          <Text type="secondary">{result.summary || '前后端代码扫描已完成。'}</Text>
        </div>
        <Tag>{issueCount} 个问题</Tag>
      </div>

      <div className={cx('workflow-code-review-targets')}>
        {targets.map((target, index) => (
          <div className={cx('workflow-code-review-target')} key={`${target.side || 'target'}-${index}`}>
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
              <div className={cx('workflow-code-review-issue')} key={issue.id || `${issue.file}-${index}`}>
                <div className={cx('workflow-code-review-issue-header')}>
                  <Tag color={issue.severity === 'critical' || issue.severity === 'high' ? 'red' : 'gold'}>
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
          <div className={cx('workflow-code-review-actions')}>
            <Button onClick={() => undefined}>修复</Button>
            <Button onClick={() => undefined}>忽略</Button>
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
