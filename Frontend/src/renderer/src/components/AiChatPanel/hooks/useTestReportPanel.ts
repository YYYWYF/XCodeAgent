import { useEffect, useRef, useState } from 'react'
import { readWorkspaceFile } from '../../../service/workspaceTools'
import type { WorkflowRunPayload, WorkflowTestReportResult } from '../../../typings'
import { formatError } from '../../Welcome/utils'
import type { RightPanelState } from '../types'

type Params = {
  activeWorkflowPhase: string
  applicationId: string
  isApplicationPlanningPhase: boolean
  /**
   * 当前工作台阶段是否为「测试阶段」。报告 tab 只在测试阶段出现，所以可用性必须
   * 一并按阶段门控 —— 只隐藏 tab 不够：离开阶段后 rightPanel.type 仍可能停在
   * test-report，渲染块按 type 判断会继续渲染报告，而 tab 条里已没有对应 tab。
   */
  reportPhaseActive: boolean
  ensureRightPanelVisible: () => void
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

/** 以当前集成测试的完成事件为准，兼顾确认门快照，避免等待下一节点才打开报告。 */
export function workflowTestReportCompleted(
  workflow: WorkflowRunPayload | undefined,
  activeWorkflowPhase: string
): boolean {
  if (!workflow) return false
  if (workflow.summary.phase === 'review_phase_confirmation') return true
  const integrationEvent = [...workflow.events]
    .reverse()
    .find(
      (event) =>
        (event.node?.id || event.nodeName) === 'integration_test' &&
        ['workflow.node.started', 'workflow.node.completed'].includes(event.type)
    )
  // 新一轮测试已开始时，不能把续跑快照中保留的旧确认门当作本轮完成。
  if (integrationEvent) return integrationEvent.type === 'workflow.node.completed'
  return [
    workflow.summary.phase,
    workflow.state?.phase,
    workflow.result?.phase,
    activeWorkflowPhase
  ].includes('review_phase_confirmation')
}

/** 为当前报告生成一次自动导航请求；同一运行处理后保留用户手动切换或关闭的选择。 */
export function nextTestReportFocusKey({
  applicationId,
  available,
  completed,
  handledKey,
  path,
  workflowRunId
}: {
  applicationId: string
  available: boolean
  completed: boolean
  handledKey: string
  path: string
  workflowRunId?: string
}): string | undefined {
  if (!available || !completed) return undefined
  const key = testReportFocusKey(applicationId, workflowRunId, path)
  return key !== handledKey ? key : undefined
}

/** 管理测试报告读取、Tab 可用状态和测试阶段完成后的右侧聚焦。 */
export function useTestReportPanel({
  activeWorkflowPhase,
  applicationId,
  isApplicationPlanningPhase,
  reportPhaseActive,
  ensureRightPanelVisible,
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
  // 可用性 = 报告已落盘 **且** 当前处在测试阶段；阶段不符时置为不可用，
  // 下方重置 effect 才会把面板从 test-report 挪走。
  const available = Boolean(path) && reportPhaseActive
  const testPhaseCompleted = workflowTestReportCompleted(workflow, activeWorkflowPhase)

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
  }, [available, path, testPhaseCompleted, workflow?.runId, workspaceRoot])

  useEffect(() => {
    if (!isApplicationPlanningPhase && rightPanel?.type === 'test-report' && !available) {
      // 开发阶段已无「文档」tab，回落到「开发产物」。
      setRightPanel({ type: 'outline' })
    }
  }, [available, isApplicationPlanningPhase, rightPanel, setRightPanel])

  // 本轮报告就绪后主动展开右侧并聚焦一次，后续更新不抢走用户选择的 tab。
  useEffect(() => {
    // 报告失效会由上方 effect 自动退回开发产物；不能继续保留已导航标记，
    // 否则同一 Run 的公开投影恢复或重新测试完成后会永久跳过报告导航。
    // 用户手动切换或关闭面板不会改变报告就绪状态，因此仍保留其选择。
    if (!available || !testPhaseCompleted) {
      handledReportRef.current = ''
      return
    }
    const reportKey = nextTestReportFocusKey({
      applicationId,
      available,
      completed: testPhaseCompleted,
      handledKey: handledReportRef.current,
      path,
      workflowRunId: workflow?.runId
    })
    if (!reportKey) return
    // 请求切换不等于已完成导航；只有渲染状态确认报告可见后才消耗本轮聚焦。
    // 同批次的默认面板回退若覆盖了请求，下一次渲染仍会继续完成这次导航。
    if (rightPanelOpen && rightPanel?.type === 'test-report') {
      handledReportRef.current = reportKey
      return
    }
    ensureRightPanelVisible()
    if (rightPanel?.type !== 'test-report') setRightPanel({ type: 'test-report' })
  }, [
    applicationId,
    available,
    ensureRightPanelVisible,
    path,
    rightPanel,
    rightPanelOpen,
    setRightPanel,
    testPhaseCompleted,
    workflow?.runId
  ])

  return { available, content, error, loading, path }
}
