export type WorkbenchEntryFailure =
  | 'workspace_missing'
  | 'configuration'
  | 'planning'
  | 'session_history'
  | 'session_missing'
  | 'unknown'

export const WORKBENCH_ENTRY_TIMEOUT_MS = 60_000

const messages: Record<WorkbenchEntryFailure, string> = {
  workspace_missing: '未找到应用的项目位置，请重新选择项目后打开。',
  configuration: '无法读取应用配置，请确认项目文件完整后重试。',
  planning: '无法加载应用的规划内容，请重新打开应用；若仍失败，请检查项目文件是否完整。',
  session_history: '无法恢复历史对话，请重新打开应用后重试。',
  session_missing: '要恢复的对话已不存在，请重新打开应用并选择其他对话。',
  unknown: '工作台暂时无法打开，请稍后重试。'
}

// 入口只显示预定义的业务提示，不将底层异常、文件路径或协议内容展示给用户。
export function workbenchEntryFailureMessage(reason: WorkbenchEntryFailure): string {
  return messages[reason] ?? messages.unknown
}

export const MISSING_RESTORE_SESSION_MESSAGE = messages.session_missing

// 只识别已知的会话不存在提示，其余异常统一归为历史对话恢复失败。
export function workbenchSessionEntryFailure(error: string): WorkbenchEntryFailure {
  return error === MISSING_RESTORE_SESSION_MESSAGE ? 'session_missing' : 'session_history'
}

export type WorkbenchEntryProgress = {
  configuration: boolean
  planning: boolean
  sessionHistory: boolean
}

// 根据真实未完成的入口步骤解释超时，不推测网络或文件损坏等根因。
export function workbenchEntryTimeoutMessage(progress: WorkbenchEntryProgress): string {
  const pending = [
    !progress.configuration && '应用配置',
    !progress.planning && '规划内容',
    !progress.sessionHistory && '历史对话'
  ].filter(Boolean)
  return pending.length
    ? `进入工作台已等待超过 60 秒，${pending.join('、')}仍未加载完成，请稍后重试。`
    : '进入工作台已等待超过 60 秒，仍未完成加载，请稍后重试。'
}
