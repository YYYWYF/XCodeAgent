export type ApplicationTheme = 'dark' | 'light'

type ApplicationSettingsPayload = {
  settings?: {
    appearance?: {
      theme?: unknown
    }
  }
}

let cachedTheme: ApplicationTheme = 'light'
let loadPromise: Promise<ApplicationTheme> | undefined

/** 将未知主题值规范化为应用支持的浅色或深色主题。 */
function normalizeTheme(value: unknown): ApplicationTheme {
  return value === 'dark' ? 'dark' : 'light'
}

/** 返回当前进程已缓存的主题，供首次 React 状态同步初始化。 */
export function getCachedApplicationTheme(): ApplicationTheme {
  return cachedTheme
}

/** 从 Electron 用户设置中读取一次应用主题，并复用并发请求。 */
export function loadApplicationTheme(): Promise<ApplicationTheme> {
  if (loadPromise) return loadPromise
  const settingsApi = window.xcodeAgent?.settings
  if (!settingsApi) return Promise.resolve(cachedTheme)
  loadPromise = settingsApi
    .load()
    .then((payload: ApplicationSettingsPayload) => {
      cachedTheme = normalizeTheme(payload.settings?.appearance?.theme)
      return cachedTheme
    })
    .catch((error: unknown) => {
      console.warn('读取应用主题失败，使用默认浅色主题。', error)
      return cachedTheme
    })
  return loadPromise
}

/** 将主题写入 Electron 用户设置；浏览器调试环境仅更新当前进程缓存。 */
export async function saveApplicationTheme(theme: ApplicationTheme): Promise<void> {
  cachedTheme = theme
  const settingsApi = window.xcodeAgent?.settings
  if (!settingsApi) return
  await settingsApi.saveTheme({ theme })
}

/** 订阅主进程广播的主题变化，并返回取消订阅函数。 */
export function subscribeApplicationTheme(
  listener: (theme: ApplicationTheme) => void
): () => void {
  const settingsApi = window.xcodeAgent?.settings
  if (!settingsApi) return () => undefined
  return settingsApi.onThemeChanged((payload) => {
    cachedTheme = normalizeTheme(payload.theme)
    listener(cachedTheme)
  })
}
