import { app, autoUpdater as nativeUpdater } from 'electron'
import path from 'node:path'
import electronUpdater from 'electron-updater'
import type { AppUpdater, ProgressInfo } from 'electron-updater'
import type { UpdateDriver, UpdateProgress } from './coordinator'
import { assertUpdateFiles } from './manifest'
import { configureUpdateFeed } from './configureFeed'
import { quitAndInstallUpdate } from './installUpdate'

/** 按平台创建 updater，使用 Java 返回的基础地址而非猜测安装包名称。 */
export function createUpdaterDriver(baseUrl: string): UpdateDriver {
  const { MacUpdater, NsisUpdater, CancellationToken } = electronUpdater
  if (!['darwin', 'win32'].includes(process.platform)) throw new Error('当前系统不支持应用自动更新')
  const updater: AppUpdater =
    process.platform === 'darwin'
      ? new MacUpdater({ provider: 'generic', url: baseUrl })
      : new NsisUpdater({ provider: 'generic', url: baseUrl })
  updater.autoDownload = false
  updater.autoInstallOnAppQuit = false
  updater.autoRunAppAfterInstall = true
  updater.forceDevUpdateConfig = !app.isPackaged
  configureUpdateFeed(
    updater,
    baseUrl,
    app.isPackaged ? undefined : path.join(app.getAppPath(), 'dev-app-update.yml')
  )
  // Java 统一决定预发布版资格，始终从同一发布清单读取，设置 channel 后再次禁止降级。
  updater.channel = 'latest'
  updater.allowPrerelease = updater.currentVersion.prerelease.length > 0
  updater.allowDowngrade = false
  const cancellation = new CancellationToken()
  let disposed = false
  let progressListener: ((progress: ProgressInfo) => void) | undefined
  /** updater 的错误也可能来自原生安装阶段，始终保留一个消费入口。 */
  const consumeError = (error: Error): void => {
    console.warn('应用更新失败：', error.message)
  }
  updater.on('error', consumeError)

  return {
    /** 下载前检查清单内容；关闭自动下载保证版本核对前不会传输安装包。 */
    async check() {
      const result = await updater.checkForUpdates()
      if (!result || disposed) return null
      assertUpdateFiles(result.updateInfo, process.platform, process.arch)
      return { version: result.updateInfo.version }
    },
    /** 将 AbortSignal 转换为 updater 的取消令牌，并只订阅本轮进度。 */
    async download(signal: AbortSignal, progress: (value: UpdateProgress) => void) {
      /** 终止当前下载，阻止超时后继续写入更新缓存。 */
      const cancel = (): void => {
        cancellation.cancel()
      }
      progressListener = (value): void => {
        if (!disposed) progress(value)
      }
      updater.on('download-progress', progressListener)
      signal.addEventListener('abort', cancel, { once: true })
      if (signal.aborted) cancel()
      try {
        await updater.downloadUpdate(cancellation)
      } finally {
        signal.removeEventListener('abort', cancel)
        if (progressListener) updater.removeListener('download-progress', progressListener)
      }
    },
    /** 原生准备完成才允许退出，Mac 签名错误可在应用仍运行时反馈。 */
    install() {
      if (!app.isPackaged || disposed) return Promise.reject(new Error('开发模式不执行安装'))
      return new Promise<void>((resolve, reject) => {
        /** 移除本次安装交接订阅，避免错误与退出事件重复处理。 */
        const cleanup = (): void => {
          updater.removeListener('error', failed)
          nativeUpdater.removeListener('before-quit-for-update', ready)
        }
        /** 原生安装器已接管退出流程。 */
        const ready = (): void => {
          cleanup()
          resolve()
        }
        /** 原生安装器接管前失败，交回协调器放行登录。 */
        const failed = (error: Error): void => {
          cleanup()
          reject(error)
        }
        updater.once('error', failed)
        nativeUpdater.once('before-quit-for-update', ready)
        try {
          quitAndInstallUpdate(updater, process.platform, process.execPath)
        } catch (error) {
          failed(error instanceof Error ? error : new Error('无法启动安装器'))
        }
      })
    },
    /** 回收应用层下载订阅；updater 自身缓存与原生服务随应用生命周期释放。 */
    dispose() {
      disposed = true
      cancellation.cancel()
      if (progressListener) updater.removeListener('download-progress', progressListener)
    }
  }
}
