import type { ApplicationConfig } from '../typings'

type WorkspaceApplication = Pick<ApplicationConfig, 'id'> & Partial<Pick<ApplicationConfig, 'workspaceRoot'>>

/** 规范化桌面提供的绝对目录，用于内存隔离；不改变持久化路径或项目身份。 */
export function normalizeWorkspaceRoot(root: string | undefined): string {
  const path = (root || '').trim().replace(/\\/g, '/').replace(/\/+$/, '') || (root === '/' ? '/' : '')
  return /^[a-z]:/i.test(path) ? path.toLowerCase() : path
}

/** 项目业务身份与本机目录共同定位一个工作区实例。 */
export function applicationWorkspaceKey(application: WorkspaceApplication): string {
  return JSON.stringify([application.id, normalizeWorkspaceRoot(application.workspaceRoot)])
}

/** 只允许同一项目的同一目录复用工作台、规划状态及异步回调。 */
export function sameApplicationWorkspace(left: WorkspaceApplication | undefined, right: WorkspaceApplication | undefined): boolean {
  return Boolean(left && right && applicationWorkspaceKey(left) === applicationWorkspaceKey(right))
}
