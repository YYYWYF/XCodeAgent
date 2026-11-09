import { BrowserWindow, ipcMain } from 'electron'
import { backendConnectionMonitor } from './backendService'

/** 桌面基础设施仅公开连接快照；生成、确认和恢复动作仍走原 AG-UI 协议。 */
export function setupBackendConnectionIpc(): void {
  ipcMain.handle('backend-connection:get', () => backendConnectionMonitor.getState())
  backendConnectionMonitor.subscribe((state) => {
    for (const window of BrowserWindow.getAllWindows()) {
      if (!window.isDestroyed()) window.webContents.send('backend-connection:changed', state)
    }
  })
}
