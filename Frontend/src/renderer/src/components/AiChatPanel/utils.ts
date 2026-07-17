import { MIN_ASSISTANT_PANEL_WIDTH, MIN_RIGHT_PANEL_WIDTH, SPLIT_HANDLE_WIDTH } from './constants'
import type {
  WorkflowRunPayload,
  WorkspaceCodeChangeFile,
  WorkspaceCodeChangeSet
} from '../../typings'

export type GroupedWorkspaceCodeChange = {
  path: string
  additions: number
  deletions: number
  changeType: WorkspaceCodeChangeFile['changeType']
  changes: WorkspaceCodeChangeFile[]
}

export type WorkspaceCodeChangeSummary = {
  files: number
  additions: number
  deletions: number
}

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

/** 判断变更路径是否属于工作目录内的 Agent 内部状态目录。 */
export function isInternalWorkspaceChangePath(path: string): boolean {
  const segments = path
    .replaceAll('\\', '/')
    .split('/')
    .filter((segment) => Boolean(segment) && segment !== '.')
  return segments.includes('.xcodeagent')
}

/** 过滤内部状态文件，并按路径合并同一次运行中的多段文件变更。 */
export function groupWorkspaceCodeChanges(
  files: WorkspaceCodeChangeFile[]
): GroupedWorkspaceCodeChange[] {
  const grouped = new Map<string, GroupedWorkspaceCodeChange>()

  files.forEach((file) => {
    if (isInternalWorkspaceChangePath(file.path)) return

    const current = grouped.get(file.path)
    if (!current) {
      grouped.set(file.path, {
        path: file.path,
        additions: file.additions,
        deletions: file.deletions,
        changeType: file.changeType,
        changes: [file]
      })
      return
    }

    current.additions += file.additions
    current.deletions += file.deletions
    current.changes.push(file)
    if (current.changeType !== 'deleted') current.changeType = file.changeType
  })

  return Array.from(grouped.values())
}

/** 汇总过滤后的可审阅文件数量与增删行数。 */
export function summarizeWorkspaceCodeChanges(
  files: GroupedWorkspaceCodeChange[]
): WorkspaceCodeChangeSummary {
  return files.reduce<WorkspaceCodeChangeSummary>(
    (summary, file) => ({
      files: summary.files + 1,
      additions: summary.additions + file.additions,
      deletions: summary.deletions + file.deletions
    }),
    { files: 0, additions: 0, deletions: 0 }
  )
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
