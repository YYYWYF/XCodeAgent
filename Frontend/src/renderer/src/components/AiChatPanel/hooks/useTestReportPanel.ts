import { useEffect, useRef, useState } from 'react'
import { readWorkspaceFile } from '../../../service/workspaceTools'
import type { WorkflowRunPayload, WorkflowTestReportResult } from '../../../typings'
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

/** 生成一次测试运行的报告聚焦键，支持同一路径在新运行完成后再次聚焦。 */
export function testReportFocusKey(
  applicationId: string,
  workflowRunId: string | undefined,
  path: string
): string {
  return `${applicationId}:${workflowRunId || 'unknown-run'}:${path}`
}

/** 从 Workflow 摘要、状态或结果中读取最新测试报告信息。 */
function readWorkflowTestReportResult(
  workflow?: WorkflowRunPayload
): WorkflowTestReportResult | undefined {
  if (!workflow) return undefined
  const candidates: unknown[] = [
    workflow.summary.testReportResult,
    workflow.state?.testReportResult,
    workflow.result?.testReportResult
  ]
  return candidates.find(
    (value): value is WorkflowTestReportResult =>
      Boolean(value) &&
      typeof value === 'object' &&
      Boolean((value as WorkflowTestReportResult).reportPath)
  )
}

/** 管理测试报告读取、Tab 可用状态和测试阶段完成后的右侧聚焦。 */
export function useTestReportPanel({
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
  const reportResult = readWorkflowTestReportResult(workflow)
  const path = String(reportResult?.reportPath || '').trim()
  const available = Boolean(path)
  const testPhaseCompleted = [workflow?.summary?.phase, activeWorkflowPhase].some(
    (phase) => String(phase || '') === 'review_phase_confirmation'
  )

  // 报告路径只来自受控 AG-UI 投影；读取失败保留独立错误态，不影响测试矩阵。
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
        setError(formatError(reason, '测试报告读取失败，请稍后重试。'))
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
    if (!isApplicationPlanningPhase && rightPanel?.type === 'test-report' && !available) {
      setRightPanel({ type: 'doc' })
    }
  }, [available, isApplicationPlanningPhase, rightPanel, setRightPanel])

  // 测试完成后只聚焦已打开的右侧面板；关闭后重开仍优先展示最新测试报告。
  useEffect(() => {
    if (!available || !testPhaseCompleted || !rightPanelOpen) return
    const reportKey = testReportFocusKey(applicationId, workflow?.runId, path)
    if (handledReportRef.current === reportKey && rightPanel) return
    handledReportRef.current = reportKey
    if (rightPanel?.type !== 'test-report') setRightPanel({ type: 'test-report' })
  }, [
    applicationId,
    available,
    path,
    rightPanel,
    rightPanelOpen,
    setRightPanel,
    testPhaseCompleted,
    workflow?.runId
  ])

  return { available, content, error, loading, path }
}
