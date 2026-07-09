import { MIN_ASSISTANT_PANEL_WIDTH, MIN_RIGHT_PANEL_WIDTH, SPLIT_HANDLE_WIDTH } from './constants'
import type { WorkflowRunPayload, WorkspaceCodeChangeSet } from '../../typings'

export function formatSessionTime(value: number): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '未知时间'
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  })
}

export function clampAssistantPanelWidth(nextWidth: number, panel: HTMLElement | null): number {
  const panelWidth = panel?.getBoundingClientRect().width ?? 0
  const maxWidth = Math.max(
    MIN_ASSISTANT_PANEL_WIDTH,
    panelWidth - MIN_RIGHT_PANEL_WIDTH - SPLIT_HANDLE_WIDTH
  )

  return Math.min(Math.max(nextWidth, MIN_ASSISTANT_PANEL_WIDTH), maxWidth)
}

export function stoppedAnswer(content: string): string {
  const trimmedContent = content.trim()
  return trimmedContent ? `${trimmedContent}\n\n_已停止生成。_` : '_已停止生成。_'
}

export function workflowCodeChanges(
  workflow: WorkflowRunPayload | undefined
): WorkspaceCodeChangeSet | undefined {
  if (!workflow) return undefined
  if (workflow.codeChanges?.files?.length) return workflow.codeChanges

  const stateCodeChanges = workflow.state?.codeChanges
  if (
    stateCodeChanges &&
    typeof stateCodeChanges === 'object' &&
    Array.isArray((stateCodeChanges as Partial<WorkspaceCodeChangeSet>).files) &&
    (stateCodeChanges as Partial<WorkspaceCodeChangeSet>).files?.length
  ) {
    return stateCodeChanges as WorkspaceCodeChangeSet
  }

  return undefined
}
