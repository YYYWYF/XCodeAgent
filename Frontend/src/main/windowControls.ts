import { ipcMain, type BrowserWindow, type IpcMainInvokeEvent } from 'electron'
import { WINDOW_CONTROLS_CHANNELS } from '../shared/windowControls'

/** 注册主窗口专用的窗口操作，校验请求来自该窗口的顶层页面。 */
export function setupWindowControlsIpc(getMainWindow: () => BrowserWindow | null): void {
  /** 将请求绑定到当前主窗口，防止其他窗口及内嵌页面调用。 */
  function requireMainWindow(event: IpcMainInvokeEvent): BrowserWindow {
    const window = getMainWindow()
    if (
      !window ||
      window.isDestroyed() ||
      event.sender !== window.webContents ||
      event.senderFrame !== window.webContents.mainFrame
    ) {
      throw new Error('主窗口不可用')
    }
    return window
  }

  ipcMain.handle(WINDOW_CONTROLS_CHANNELS.getMaximized, (event): boolean =>
    requireMainWindow(event).isMaximized()
  )
  ipcMain.handle(WINDOW_CONTROLS_CHANNELS.perform, (event, action: unknown): void => {
    const window = requireMainWindow(event)
    switch (action) {
      case 'minimize':
        window.minimize()
        break
      case 'toggle-maximize':
        if (window.isMaximized()) window.unmaximize()
        else window.maximize()
        break
      case 'close':
        window.close()
        break
      default:
        throw new TypeError('不支持的窗口操作')
    }
  })
}

/** 将系统触发的最大化和还原同步到同一窗口的自绘按钮。 */
export function attachWindowControls(window: BrowserWindow): void {
  /** 将 Electron 当前窗口状态发送给主窗口的页面。 */
  function publishMaximized(): void {
    if (!window.isDestroyed() && !window.webContents.isDestroyed()) {
      window.webContents.send(WINDOW_CONTROLS_CHANNELS.maximizedChanged, window.isMaximized())
    }
  }

  window.on('maximize', publishMaximized)
  window.on('unmaximize', publishMaximized)
}
