import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import {
  getCachedApplicationTheme,
  saveApplicationTheme,
  subscribeApplicationTheme,
  type ApplicationTheme
} from '../service/applicationSettings'

type ApplicationThemeContextValue = {
  theme: ApplicationTheme
  setTheme: (theme: ApplicationTheme) => void
}

const ApplicationThemeContext = createContext<ApplicationThemeContextValue | undefined>(undefined)

/** 在应用根部持有主题状态，并同步来自其他 Electron 窗口的设置变化。 */
export function ApplicationThemeProvider({ children }: { children: ReactNode }): JSX.Element {
  const [theme, setThemeState] = useState<ApplicationTheme>(getCachedApplicationTheme)

  useEffect(() => subscribeApplicationTheme(setThemeState), [])

  // 乐观更新当前窗口，并异步持久化应用级主题。
  const setTheme = useCallback((nextTheme: ApplicationTheme): void => {
    setThemeState(nextTheme)
    void saveApplicationTheme(nextTheme).catch((error: unknown) => {
      console.warn('保存应用主题失败。', error)
    })
  }, [])

  return (
    <ApplicationThemeContext.Provider value={{ theme, setTheme }}>
      {children}
    </ApplicationThemeContext.Provider>
  )
}

/** 读取应用根部共享的主题状态和切换动作。 */
export function useApplicationTheme(): ApplicationThemeContextValue {
  const context = useContext(ApplicationThemeContext)
  if (!context) {
    throw new Error('useApplicationTheme must be used within ApplicationThemeProvider')
  }
  return context
}
