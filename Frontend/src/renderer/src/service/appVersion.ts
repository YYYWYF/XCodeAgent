let versionPromise: Promise<string> | undefined

/** 为当前客户端版本添加统一的小写 v 前缀，保留预发布和构建标识。 */
export function formatAppVersion(value: unknown): string {
  if (typeof value !== 'string') return ''
  const version = value.trim().replace(/^v/i, '')
  return version ? `v${version}` : ''
}

/** 每个渲染进程只读取一次客户端版本；读取失败时隐藏标签且不阻塞页面。 */
export function loadAppVersion(): Promise<string> {
  if (versionPromise) return versionPromise
  const api = window.devAgentStudio
  if (!api?.getAppVersion) return Promise.resolve('')

  versionPromise = Promise.resolve()
    .then(() => api.getAppVersion())
    .then(formatAppVersion)
    .catch(() => '')
  return versionPromise
}
