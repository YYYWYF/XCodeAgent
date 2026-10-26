import { clipboard, ipcMain } from 'electron'

/** 注册受控的文本复制入口，避免桌面复制依赖浏览器焦点和剪贴板权限。 */
export function setupClipboardIpc(): void {
  ipcMain.handle('clipboard:write-text', (_event, text: unknown): void => {
    if (typeof text !== 'string') throw new TypeError('Clipboard text must be a string')
    clipboard.writeText(text)
  })
}
