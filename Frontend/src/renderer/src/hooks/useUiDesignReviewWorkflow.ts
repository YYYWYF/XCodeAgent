import { useRef } from 'react'
import {
  planningWorkflowClarification,
  planningWorkflowPhase,
  planningWorkflowUiDesignSkipped
} from '../service/applicationPlanningWorkflowState'
import type { WorkflowRunPayload } from '../typings'

type ReviewPages = {
  workspaceRoot: string | undefined
  threadId: string
  pages: unknown[]
}

/** 刷新中只保留同一工作区和线程的页面展示，不缓存运行身份或确认门。 */
export function useUiDesignReviewWorkflow(
  workflow: WorkflowRunPayload,
  workspaceRoot?: string
): WorkflowRunPayload {
  const previous = useRef<ReviewPages>()
  const clarification = planningWorkflowClarification(workflow)
  const currentClarification =
    workflow.summary.clarification ??
    workflow.state?.clarification ??
    workflow.result?.clarification
  const currentMode =
    currentClarification && typeof currentClarification === 'object'
      ? (currentClarification as Record<string, unknown>).mode
      : undefined
  const phase = planningWorkflowPhase(workflow)
  const sameScope =
    previous.current?.workspaceRoot === workspaceRoot &&
    previous.current?.threadId === workflow.threadId
  const isUiReview =
    (!phase || phase === 'ui_confirmation') &&
    (!currentMode || currentMode === 'ui_design_confirmation') &&
    !planningWorkflowUiDesignSkipped(workflow)
  if (!sameScope || !isUiReview) previous.current = undefined
  const pages = clarification?.pages
  if (
    isUiReview &&
    clarification?.mode === 'ui_design_confirmation' &&
    Array.isArray(pages) &&
    pages.length > 0
  ) {
    previous.current = { workspaceRoot, threadId: workflow.threadId, pages }
    return workflow
  }
  if (!isUiReview || workflow.summary.status !== 'running') {
    previous.current = undefined
    return workflow
  }
  if (!previous.current) return workflow
  // 当前 run 的其他字段完全保留；缓存仅供禁用中的审阅面板继续显示页面。
  return {
    ...workflow,
    summary: {
      ...workflow.summary,
      clarification: {
        ...clarification,
        mode: 'ui_design_confirmation',
        status: 'running',
        pages: previous.current.pages
      }
    }
  }
}
