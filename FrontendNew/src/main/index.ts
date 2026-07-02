import { app, shell, BrowserWindow, Menu, Tray } from 'electron'
import { join } from 'path'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import icon from '../../resources/icon.png?asset'
import { clearAuthToken, getStoredToken } from './auth'
import { registerIpcHandlers } from './ipc'
import { ensureXcodeAgentHome } from './xcodeagent-home'

let mainWindow: BrowserWindow | null = null
let loginWindow: BrowserWindow | null = null
let tray: Tray | null = null
let isQuitting = false

const mainRendererFilePath = join(__dirname, '../renderer/index.html')
const loginRendererFilePath = join(__dirname, '../renderer/login.html')
const preloadFilePath = join(__dirname, '../preload/index.js')

const gotSingleInstanceLock = app.requestSingleInstanceLock()

if (!gotSingleInstanceLock) {
  app.quit()
}

const getDevRendererUrl = (pathname = ''): string => {
  const rendererUrl = process.env['ELECTRON_RENDERER_URL']

  if (!rendererUrl) {
    return ''
  }

  return new URL(pathname, rendererUrl).toString()
}

const loadMainWindowRoute = (window: BrowserWindow, route: string): void => {
  const devRendererUrl = getDevRendererUrl()

  if (is.dev && devRendererUrl) {
    const mainUrl = `${devRendererUrl}#${route}`

    console.info(`[main] load main window: ${mainUrl}`)
    void window.loadURL(mainUrl)
    return
  }

  console.info(`[main] load main window file: ${mainRendererFilePath}#${route}`)
  void window.loadFile(mainRendererFilePath, { hash: route })
}

const loadLoginPage = (window: BrowserWindow): void => {
  const devLoginUrl = getDevRendererUrl('login.html')

  if (is.dev && devLoginUrl) {
    console.info(`[main] load login window: ${devLoginUrl}`)
    void window.loadURL(devLoginUrl)
    return
  }

  console.info(`[main] load login window file: ${loginRendererFilePath}`)
  void window.loadFile(loginRendererFilePath)
}

const focusWindow = (window: BrowserWindow): void => {
  if (window.isMinimized()) {
    window.restore()
  }

  window.show()
  window.focus()
}

const openExternalLinksInBrowser = (window: BrowserWindow): void => {
  window.webContents.setWindowOpenHandler((details) => {
    shell.openExternal(details.url)
    return { action: 'deny' }
  })

  window.webContents.on('did-fail-load', (_, errorCode, errorDescription, validatedURL) => {
    console.error(`Failed to load ${validatedURL}: ${errorCode} ${errorDescription}`)
  })

  window.webContents.on('render-process-gone', (_, details) => {
    console.error(`Renderer process gone: ${details.reason}`)
  })
}

const createTray = (): void => {
  if (tray) {
    return
  }

  tray = new Tray(icon)
  tray.setToolTip('XcodeAgent')
  tray.setContextMenu(
    Menu.buildFromTemplate([
      {
        label: '显示主窗口',
        click: () => {
          void showMainWindow()
        }
      },
      { type: 'separator' },
      {
        label: '退出',
        click: () => {
          void quitFromTray()
        }
      }
    ])
  )
  tray.on('click', () => {
    void showMainWindow()
  })
}

const createMainWindow = (): BrowserWindow => {
  if (mainWindow) {
    return mainWindow
  }

  createTray()

  mainWindow = new BrowserWindow({
    width: 1300,
    height: 800,
    show: false,
    autoHideMenuBar: true,
    ...(process.platform === 'linux' ? { icon } : {}),
    webPreferences: {
      preload: preloadFilePath,
      sandbox: false
    }
  })

  mainWindow.on('ready-to-show', () => {
    mainWindow?.show()
  })

  mainWindow.on('close', (event) => {
    if (!isQuitting) {
      event.preventDefault()
      mainWindow?.hide()
    }
  })

  mainWindow.on('closed', () => {
    mainWindow = null
  })

  openExternalLinksInBrowser(mainWindow)
  loadMainWindowRoute(mainWindow, '/skill')

  return mainWindow
}

const createLoginWindow = (): BrowserWindow => {
  if (loginWindow) {
    return loginWindow
  }

  loginWindow = new BrowserWindow({
    width: 420,
    height: 300,
    show: false,
    resizable: false,
    maximizable: false,
    autoHideMenuBar: true,
    title: '登录',
    ...(process.platform === 'linux' ? { icon } : {}),
    webPreferences: {
      preload: preloadFilePath,
      sandbox: false
    }
  })

  loginWindow.on('ready-to-show', () => {
    loginWindow?.show()
  })

  loginWindow.on('close', () => {
    if (!isQuitting && !mainWindow) {
      isQuitting = true
      app.quit()
    }
  })

  loginWindow.on('closed', () => {
    loginWindow = null
  })

  openExternalLinksInBrowser(loginWindow)
  loadLoginPage(loginWindow)

  return loginWindow
}

const showMainWindow = async (): Promise<void> => {
  const token = await getStoredToken()

  if (!token) {
    const nextLoginWindow = createLoginWindow()
    focusWindow(nextLoginWindow)
    return
  }

  const nextMainWindow = createMainWindow()
  focusWindow(nextMainWindow)
}

const showInitialWindow = async (): Promise<void> => {
  const token = await getStoredToken()

  if (token) {
    console.info('[main] stored token found, opening main window')
    createMainWindow()
    return
  }

  console.info('[main] no stored token, opening login window')
  createLoginWindow()
}

const handleLoginSuccess = (): void => {
  createMainWindow()

  if (loginWindow && !loginWindow.isDestroyed()) {
    loginWindow.close()
  }
}

const quitFromTray = async (): Promise<void> => {
  isQuitting = true

  try {
    await clearAuthToken()
  } catch (error) {
    console.error(error)
  } finally {
    tray?.destroy()
    tray = null
    app.quit()
  }
}

if (gotSingleInstanceLock) {
  app.on('second-instance', () => {
    if (mainWindow) {
      focusWindow(mainWindow)
      return
    }

    if (loginWindow) {
      focusWindow(loginWindow)
      return
    }

    void showInitialWindow()
  })

  app.whenReady().then(async () => {
    electronApp.setAppUserModelId('com.electron')

    app.on('browser-window-created', (_, window) => {
      optimizer.watchWindowShortcuts(window)
    })

    try {
      await ensureXcodeAgentHome()
    } catch (error) {
      console.error('[main] failed to initialize XcodeAgent home', error)
    }

    registerIpcHandlers({
      onLoginSuccess: handleLoginSuccess
    })

    void showInitialWindow()

    app.on('activate', () => {
      if (mainWindow) {
        focusWindow(mainWindow)
        return
      }

      if (loginWindow) {
        focusWindow(loginWindow)
        return
      }

      void showInitialWindow()
    })
  })
}
