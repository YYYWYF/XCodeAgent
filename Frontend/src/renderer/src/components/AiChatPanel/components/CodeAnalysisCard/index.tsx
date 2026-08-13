import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  DownOutlined,
  FileSearchOutlined,
  ReloadOutlined,
  UpOutlined
} from '@ant-design/icons'
import { Button, Progress, Spin, Tag, Typography } from 'antd'
import { useState, type ReactElement } from 'react'
import { getFrontendCodeAnalysisReport } from '../../../../service/codeAnalysis'
import type { CodeAnalysisResult } from '../../../../typings'
import { cx } from '../../../../utils'
import MarkdownContent from '../../../MarkdownContent/MarkdownContent'
import './CodeAnalysisCard.less'

const { Text } = Typography

type Props = {
  analysis: CodeAnalysisResult
  disabled: boolean
  onRetry: () => void
  workspaceRoot: string
}

/** 展示代码扫描阶段、统计和按需加载的 Markdown 报告。 */
export default function CodeAnalysisCard({
  analysis,
  disabled,
  onRetry,
  workspaceRoot
}: Props): ReactElement {
  const [expanded, setExpanded] = useState(false)
  const [report, setReport] = useState('')
  const [reportError, setReportError] = useState('')
  const [reportLoading, setReportLoading] = useState(false)
  const running = analysis.status === 'in_progress'
  const completed = analysis.status === 'completed'
  const failed = analysis.status === 'failed' || analysis.status === 'cancelled'

  /** 展开时按报告路径读取正文，并只在卡片内存中缓存。 */
  const toggleReport = async (): Promise<void> => {
    const nextExpanded = !expanded
    setExpanded(nextExpanded)
    if (!nextExpanded || report || !analysis.reportPath || reportLoading) return
    setReportLoading(true)
    setReportError('')
    try {
      setReport(await getFrontendCodeAnalysisReport(workspaceRoot, analysis.reportPath))
    } catch (caughtError) {
      setReportError(caughtError instanceof Error ? caughtError.message : '报告读取失败。')
    } finally {
      setReportLoading(false)
    }
  }

  return (
    <div className={cx('code-analysis-card', running && 'running', failed && 'failed')}>
      <div className={cx('code-analysis-header')}>
        <span className={cx('code-analysis-icon')}>
          {running ? (
            <Spin size="small" />
          ) : completed ? (
            <CheckCircleOutlined />
          ) : (
            <CloseCircleOutlined />
          )}
        </span>
        <div className={cx('code-analysis-title')}>
          <Text strong>前端代码扫描</Text>
          <Text type="secondary">
            {running
              ? analysis.progress?.message || '正在分析前端代码'
              : completed
                ? '扫描已完成'
                : analysis.error?.message || '扫描失败'}
          </Text>
        </div>
      </div>

      {running && (
        <Progress
          percent={analysis.progress?.percent || 0}
          showInfo={false}
          size="small"
          status="active"
        />
      )}
      {running && analysis.activeToolActivity?.message && (
        <Text className={cx('code-analysis-activity')} type="secondary">
          {analysis.activeToolActivity.message}
        </Text>
      )}

      {completed && (
        <>
          <div className={cx('code-analysis-statistics')}>
            <span>
              <strong>{analysis.scannedFiles || 0}</strong> 扫描文件
            </span>
            <span>
              <strong>{analysis.issueCount || 0}</strong> 问题
            </span>
            <span>
              <strong>{analysis.problemFileCount || 0}</strong> 问题文件
            </span>
          </div>
          <div className={cx('code-analysis-severity')}>
            <Tag>严重 {analysis.severityCounts?.critical || 0}</Tag>
            <Tag>高 {analysis.severityCounts?.high || 0}</Tag>
            <Tag>中 {analysis.severityCounts?.medium || 0}</Tag>
            <Tag>低 {analysis.severityCounts?.low || 0}</Tag>
          </div>
          {analysis.reportPath && (
            <Text className={cx('code-analysis-path')} code copyable>
              {analysis.reportPath}
            </Text>
          )}
          <Button
            className={cx('code-analysis-report-toggle')}
            icon={expanded ? <UpOutlined /> : <DownOutlined />}
            onClick={() => void toggleReport()}
            size="small"
            type="text"
          >
            {expanded ? '收起报告' : '展开报告'}
          </Button>
          {expanded && (
            <div className={cx('code-analysis-report')}>
              {reportLoading ? (
                <Spin size="small" tip="正在加载报告" />
              ) : reportError ? (
                <Text type="danger">{reportError}</Text>
              ) : (
                <MarkdownContent content={report} />
              )}
            </div>
          )}
        </>
      )}

      {failed && (
        <Button disabled={disabled} icon={<ReloadOutlined />} onClick={onRetry} size="small">
          重新扫描
        </Button>
      )}
      {!running && !completed && !failed && <FileSearchOutlined />}
    </div>
  )
}
