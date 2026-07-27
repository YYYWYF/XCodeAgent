import { app, BrowserWindow, ipcMain } from 'electron'
import crypto from 'node:crypto'
import fs from 'node:fs/promises'
import path from 'node:path'

export type ApplicationTheme = 'dark' | 'light'

type ApplicationSettings = {
  version: 1
  appearance: {
    theme: ApplicationTheme
  }
}

const DEFAULT_SETTINGS: ApplicationSettings = {
  version: 1,
  appearance: {
    theme: 'light'
  }
}

/** 返回 Electron 用户配置目录中的应用设置文件路径。 */
function getApplicationSettingsFile(): string {
  return path.join(app.getPath('userData'), 'settings.json')
}

/** 将未知设置内容规范化为当前支持的版本，损坏字段回退到安全默认值。 */
function normalizeApplicationSettings(value: unknown): ApplicationSettings {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return DEFAULT_SETTINGS
  const appearance = (value as { appearance?: unknown }).appearance
  if (!appearance || typeof appearance !== 'object' || Array.isArray(appearance)) {
    return DEFAULT_SETTINGS
  }
  const theme = (appearance as { theme?: unknown }).theme
  return {
    version: 1,
    appearance: {
      theme: theme === 'dark' ? 'dark' : 'light'
    }
  }
}

/** 读取应用级设置；文件尚未创建时返回默认设置。 */
async function readApplicationSettings(): Promise<ApplicationSettings> {
  try {
    const rawValue = await fs.readFile(getApplicationSettingsFile(), 'utf8')
    return normalizeApplicationSettings(JSON.parse(rawValue))
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return DEFAULT_SETTINGS
    console.warn('读取应用设置失败，使用默认设置。', error)
    return DEFAULT_SETTINGS
  }
}

/** 使用同目录临时文件原子写入应用级设置，避免中断时留下半份 JSON。 */
async function writeApplicationSettings(settings: ApplicationSettings): Promise<void> {
  const settingsFile = getApplicationSettingsFile()
  await fs.mkdir(path.dirname(settingsFile), { recursive: true })
  const temporaryFile = `${settingsFile}.${process.pid}.${crypto.randomUUID()}.tmp`
  try {
    await fs.writeFile(temporaryFile, `${JSON.stringify(settings, null, 2)}\n`, {
      encoding: 'utf8',
      mode: 0o600
    })
    await fs.rename(temporaryFile, settingsFile)
  } finally {
    await fs.rm(temporaryFile, { force: true }).catch(() => undefined)
  }
}

/** 向所有已打开窗口广播最新主题，保持主窗口与登录窗口一致。 */
function broadcastApplicationTheme(theme: ApplicationTheme): void {
  for (const window of BrowserWindow.getAllWindows()) {
    window.webContents.send('settings:theme-changed', { theme })
  }
}

/** 注册应用级设置的读取和主题保存 IPC。 */
export function setupApplicationSettingsIpc(): void {
  ipcMain.handle('settings:load', async () => ({
    settings: await readApplicationSettings()
  }))

  ipcMain.handle('settings:save-theme', async (_event, payload = {}) => {
    const theme = payload.theme
    if (theme !== 'dark' && theme !== 'light') {
      throw new Error('theme must be dark or light')
    }
    const currentSettings = await readApplicationSettings()
    const settings: ApplicationSettings = {
      ...currentSettings,
      appearance: {
        ...currentSettings.appearance,
        theme
      }
    }
    await writeApplicationSettings(settings)
    broadcastApplicationTheme(theme)
    return { ok: true, settings }
  })
}
