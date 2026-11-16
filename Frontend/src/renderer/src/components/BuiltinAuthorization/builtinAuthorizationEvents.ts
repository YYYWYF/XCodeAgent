// 仅传递当前工作区的失效通知，任务快照始终来自服务端。
const listeners = new Map<string, Set<() => void>>()

/** 让同一工作区的已挂载视图重读任务。 */
export function notifyBuiltinAuthorizationChanged(workspace: string): void {
  for (const listener of listeners.get(workspace) || []) {
    try { listener() } catch { /* 通知失败不影响远端动作结果。 */ }
  }
}

/** 按工作区订阅本地失效通知，卸载时释放订阅。 */
export function subscribeBuiltinAuthorizationChanged(workspace: string, listener: () => void): () => void {
  const group = listeners.get(workspace) || new Set<() => void>()
  group.add(listener)
  listeners.set(workspace, group)
  return () => {
    group.delete(listener)
    if (!group.size) listeners.delete(workspace)
  }
}

/** 检查当前可见性，异步回调需重新读取而不能沿用请求发起时的状态。 */
export function builtinAuthorizationVisible(): boolean {
  return document.visibilityState !== 'hidden'
}
