const DATA_SOURCES_CHANGED_EVENT = 'devagentstudio-data-sources-changed'

/** 仅在数据源变更已成功保存后通知同一窗口中的工作区读取者。 */
export function notifyDataSourcesChanged(workspaceRoot: string): void {
  window.dispatchEvent(new CustomEvent(DATA_SOURCES_CHANGED_EVENT, { detail: { workspaceRoot } }))
}

/** 按完整工作区身份订阅数据源变化，卸载后释放监听器。 */
export function subscribeDataSourcesChanged(
  workspaceRoot: string,
  listener: () => void
): () => void {
  /** 其他项目的变更不能触发当前工作区刷新。 */
  const handleChange = (event: Event): void => {
    const detail: unknown = (event as CustomEvent<unknown>).detail
    if (
      detail &&
      typeof detail === 'object' &&
      (detail as { workspaceRoot?: unknown }).workspaceRoot === workspaceRoot
    )
      listener()
  }
  window.addEventListener(DATA_SOURCES_CHANGED_EVENT, handleChange)
  return () => window.removeEventListener(DATA_SOURCES_CHANGED_EVENT, handleChange)
}
