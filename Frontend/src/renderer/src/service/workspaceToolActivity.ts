import type { WorkflowBuildToolActivity } from '../typings'

const TOOL_CATEGORIES: Record<string, WorkflowBuildToolActivity['category']> = {
  ls: 'browse',
  read_file: 'read',
  glob: 'search',
  grep: 'search',
  write_file: 'write',
  edit_file: 'write',
  delete_file: 'delete'
}

/** 读取已由后端脱敏的文件工具活动，过滤未知工具和不完整的实时快照。 */
export function readWorkspaceToolActivity(value: unknown): WorkflowBuildToolActivity | undefined {
  if (!value || typeof value !== 'object') return undefined
  const activity = value as Record<string, unknown>
  const { tool, status, callId, message, path } = activity
  if (
    typeof tool !== 'string' ||
    !Object.prototype.hasOwnProperty.call(TOOL_CATEGORIES, tool) ||
    (status !== 'running' && status !== 'completed' && status !== 'failed') ||
    typeof callId !== 'string' ||
    !callId.trim() ||
    typeof message !== 'string' ||
    !message.trim()
  ) {
    return undefined
  }
  return {
    callId: callId.trim().slice(0, 256),
    tool: tool as WorkflowBuildToolActivity['tool'],
    category: TOOL_CATEGORIES[tool],
    status,
    message: message.trim().slice(0, 320),
    ...(typeof path === 'string' && path.trim() ? { path: path.trim().slice(0, 260) } : {})
  }
}
