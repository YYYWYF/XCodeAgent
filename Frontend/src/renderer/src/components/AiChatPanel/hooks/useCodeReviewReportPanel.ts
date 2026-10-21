import { useEffect, useRef, useState } from 'react'
import { readWorkspaceFile } from '../../../service/workspaceTools'
import type { WorkflowCodeReviewResult, WorkflowRunPayload } from '../../../typings'
import { formatError } from '../../Welcome/utils'
import type { RightPanelState } from '../types'

type Params = {
  activeWorkflowPhase: string
  applicationId: string
  isApplicationPlanningPhase: boolean
  rightPanel?: RightPanelState
  rightPanelOpen: boolean
  setRightPanel: (panel?: RightPanelState) => void
  workflow?: WorkflowRunPayload
  workspaceRoot?: string
}

type Result = {
  available: boolean
  content: string
  error: string
  loading: boolean
  path: string
}

/** 生成一次审查运行的报告聚焦键，同一路径在新运行完成后仍会再次自动切换。 */
export function codeReviewReportFocusKey(
  applicationId: string,
  workflowRunId: string | undefined,
  path: string
): string {
  return `${applicationId}:${workflowRunId || 'unknown-run'}:${path}`
}

/** 从 Workflow 的公开摘要、状态或结果中读取最新代码审查报告信息。 */
function readWorkflowCodeReviewResult(
  workflow?: WorkflowRunPayload
): WorkflowCodeReviewResult | undefined {
  if (!workflow) return undefined
  const candidates: unknown[] = [
    workflow.summary.codeReviewResult,
    workflow.state?.codeReviewResult,
    workflow.result?.codeReviewResult
  ]
  const validCandidates = candidates.filter(
    (value): value is WorkflowCodeReviewResult =>
      Boolean(value) && typeof value === 'object' && Object.keys(value as object).length > 0
  )
  return validCandidates.find((value) => Boolean(value.reportPath)) || validCandidates[0]
}

/** 管理审查报告读取、Tab 可用状态，以及审查阶段的右侧面板聚焦行为。 */
export function useCodeReviewReportPanel({
  activeWorkflowPhase,
  applicationId,
  isApplicationPlanningPhase,
  rightPanel,
  rightPanelOpen,
  setRightPanel,
  workflow,
  workspaceRoot
}: Params): Result {
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const handledReportRef = useRef('')
  const reviewResult = readWorkflowCodeReviewResult(workflow)
  const path = String(reviewResult?.reportPath || '').trim()
  const available = Boolean(path)
  const reviewPhaseActive = [workflow?.summary?.phase, activeWorkflowPhase].some((phase) =>
    ['code_review', 'acceptance_phase_confirmation'].includes(String(phase || ''))
  )

  // 报告路径只来自受控 AG-UI 投影；读取失败保留独立错误态，不影响对话区审查结果。
  useEffect(() => {
    if (!available || !workspaceRoot) {
      setContent('')
      setError('')
      setLoading(false)
      return
    }
    let cancelled = false
    setLoading(true)
    setError('')
    void readWorkspaceFile({
      workspace_root: workspaceRoot,
      path,
      max_lines: 5_000,
      max_chars: 200_000
    })
      .then((result) => {
        if (!cancelled) setContent(result.content)
      })
      .catch((reason: unknown) => {
        if (cancelled) return
        setContent('')
        setError(formatError(reason, '审查报告读取失败，请稍后重试。'))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [available, path, workspaceRoot])

  useEffect(() => {
    if (!rightPanelOpen) handledReportRef.current = ''
  }, [rightPanelOpen])

  useEffect(() => {
    if (!isApplicationPlanningPhase && rightPanel?.type === 'review-report' && !available) {
      setRightPanel({ type: 'doc' })
    }
  }, [available, isApplicationPlanningPhase, rightPanel, setRightPanel])

  // 扫描报告首次就绪时只在已打开的右侧面板中自动切换；关闭后重开审查阶段仍优先报告。
  useEffect(() => {
    if (!available || !reviewPhaseActive || !rightPanelOpen) return
    const reportKey = codeReviewReportFocusKey(applicationId, workflow?.runId, path)
    if (handledReportRef.current === reportKey && rightPanel) return
    handledReportRef.current = reportKey
    if (rightPanel?.type !== 'review-report') setRightPanel({ type: 'review-report' })
  }, [
    applicationId,
    available,
    path,
    reviewPhaseActive,
    rightPanel,
    rightPanelOpen,
    setRightPanel,
    workflow?.runId
  ])

  return { available, content, error, loading, path }
}
