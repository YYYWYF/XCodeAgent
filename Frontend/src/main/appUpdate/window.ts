import { app, BrowserWindow, ipcMain, type IpcMainInvokeEvent } from 'electron'
import path from 'node:path'
import { PRODUCT_DISPLAY_NAME } from '../branding'
import { AppUpdateCoordinator } from './coordinator'
import { checkAppRelease } from './checkRelease'
import { createUpdaterDriver } from './updaterDriver'

let activeWindow: BrowserWindow | null = null
let activeCoordinator: AppUpdateCoordinator | undefined
const channels = [
  'app-update:state',
  'app-update:install',
  'app-update:continue',
  'app-update:quit'
]

/** 第二实例或 Dock 激活时聚焦启动更新窗口，禁止绕过更新门禁。 */
export function focusStartupUpdate(): boolean {
  if (!activeWindow || activeWindow.isDestroyed()) return false
  if (activeWindow.isMinimized()) activeWindow.restore()
  activeWindow.show()
  activeWindow.focus()
  return true
}

/** 退出清理时撤销更新，避免迟到响应再打开登录窗口。 */
export function cancelStartupUpdate(): void {
  activeCoordinator?.quit()
}

/** 先显示更新页并等待 Python 就绪，返回 true 才允许打开登录窗口。 */
export async function runStartupUpdate(
  prepareBackend: () => Promise<string>,
  prepareInstall: () => Promise<void>
): Promise<boolean> {
  const window = new BrowserWindow({
    width: 480,
    height: 300,
    resizable: false,
    maximizable: false,
    show: false,
    title: `${PRODUCT_DISPLAY_NAME} 更新`,
    backgroundColor: '#f7f5fc',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  })
  activeWindow = window
  let backendBaseUrl = ''
  let startupFailure: unknown
  const coordinator = new AppUpdateCoordinator({
    currentVersion: app.getVersion(),
    isPackaged: app.isPackaged,
    prepare: async () => {
      backendBaseUrl = await prepareBackend()
    },
    check: (signal) => checkAppRelease(backendBaseUrl, app.getVersion(), signal),
    createDriver: createUpdaterDriver,
    prepareInstall,
    publish: (state) => {
      if (state.phase === 'error') console.warn('启动更新未完成：', state.message)
      if (!window.isDestroyed()) window.webContents.send('app-update:changed', state)
    }
  })
  activeCoordinator = coordinator
  let internalClose = false
  /** 仅更新窗口能够触发更新操作，不接受其他窗口传入地址或安装文件。 */
  const authorize = (event: IpcMainInvokeEvent): void => {
    if (window.isDestroyed() || event.sender !== window.webContents)
      throw new Error('更新窗口不可用')
  }
  ipcMain.handle(channels[0], (event) => {
    authorize(event)
    return coordinator.snapshot()
  })
  ipcMain.handle(channels[1], async (event) => {
    authorize(event)
    await coordinator.install()
  })
  ipcMain.handle(channels[2], (event) => {
    authorize(event)
    coordinator.continueDevelopment()
  })
  ipcMain.handle(channels[3], (event) => {
    authorize(event)
    coordinator.quit()
  })
  window.on('close', (event) => {
    if (internalClose) return
    event.preventDefault()
    coordinator.quit()
  })
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  window.webContents.on('will-navigate', (event) => event.preventDefault())
  window.once('ready-to-show', () => window.show())
  try {
    const renderer = process.env.ELECTRON_RENDERER_URL
    if (renderer) await window.loadURL(`${renderer.replace(/\/$/, '')}/update.html`)
    else await window.loadFile(path.join(__dirname, '../renderer/update.html'))
    void coordinator.start().catch((error: unknown) => {
      startupFailure = error
      coordinator.quit()
    })
    const continueStartup = await coordinator.result
    if (startupFailure) throw startupFailure
    return continueStartup
  } catch (error) {
    if (startupFailure) throw startupFailure
    console.warn('更新窗口启动失败，继续打开应用：', error)
    coordinator.quit()
    return true
  } finally {
    internalClose = true
    for (const channel of channels) ipcMain.removeHandler(channel)
    if (!window.isDestroyed()) window.destroy()
    activeWindow = null
    activeCoordinator = undefined
  }
}
