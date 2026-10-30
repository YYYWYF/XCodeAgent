import { app, ipcMain } from 'electron'

/** 注册应用生命周期内可用的只读版本入口，与启动更新使用同一版本来源。 */
export function setupAppVersionIpc(): void {
  ipcMain.handle('app:get-version', (): string => app.getVersion())
}
