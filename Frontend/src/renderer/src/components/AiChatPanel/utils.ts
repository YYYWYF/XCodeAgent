import { MIN_ASSISTANT_PANEL_WIDTH, MIN_RIGHT_PANEL_WIDTH, SPLIT_HANDLE_WIDTH } from './constants'
import type {
  WorkflowRunPayload,
  WorkspaceCodeChangeFile,
  WorkspaceCodeChangeSet
} from '../../typings'
import { normalizePreviewUrl } from '../../utils/previewUrl'

export type WorkflowPreviewTarget = {
  key: string
  url: string
}

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

export type WorkspacePathParts = {
  directory: string
  fileName: string
}

/** 将工作区相对路径拆分为目录和文件名，供窄面板优先展示文件名。 */
export function splitWorkspacePath(path: string): WorkspacePathParts {
  const normalizedPath = path.replaceAll('\\', '/')
  const separatorIndex = normalizedPath.lastIndexOf('/')
  if (separatorIndex < 0) return { directory: '', fileName: normalizedPath }
  return {
    directory: normalizedPath.slice(0, separatorIndex),
    fileName: normalizedPath.slice(separatorIndex + 1)
  }
}

/** 拼接工作区根目录名称和文件相对路径，并兼容缺少 workspaceName 的旧记录。 */
export function workspaceCodeChangeDisplayPath(
  filePath: string,
  workspaceRoot: string,
  workspaceName?: string
): string {
  const normalizedPath = filePath.replaceAll('\\', '/').replace(/^\.\//, '')
  const normalizedRoot = workspaceRoot.replaceAll('\\', '/').replace(/\/+$/, '')
  const derivedWorkspaceName = normalizedRoot.split('/').filter(Boolean).at(-1) || ''
  const rootName = workspaceName?.trim() || derivedWorkspaceName
  if (!rootName || normalizedPath === rootName || normalizedPath.startsWith(`${rootName}/`)) {
    return normalizedPath
  }
  return `${rootName}/${normalizedPath}`
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

/** 从实时且成功的启动节点中提取一次性预览导航目标。 */
export function workflowPreviewTarget(
  workflow: WorkflowRunPayload | undefined,
  live: boolean
): WorkflowPreviewTarget | undefined {
  if (
    !live ||
    !workflow ||
    workflow.summary.phase !== 'launch_project' ||
    workflow.summary.status !== 'requires_user_input'
  ) {
    return undefined
  }

  const url = normalizePreviewUrl(workflow.summary.previewUrl || '')
  if (!url) return undefined
  return {
    key: `${workflow.threadId}:${workflow.runId}:${url}`,
    url
  }
}
