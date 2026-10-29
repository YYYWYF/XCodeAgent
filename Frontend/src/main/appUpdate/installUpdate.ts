import path from 'node:path'
import type { NsisUpdater } from 'electron-updater'

type UpdateInstaller = Pick<NsisUpdater, 'installDirectory' | 'quitAndInstall'>

/** Windows 更新静默覆盖当前程序目录并重启，macOS 保持原生安装方式。 */
export function quitAndInstallUpdate(
  updater: UpdateInstaller,
  platform: NodeJS.Platform,
  executablePath: string
): void {
  const isWindows = platform === 'win32'
  if (isWindows) {
    // 使用实际运行的 EXE 定位目录；win32 路径规则也便于在其他平台验证带空格路径。
    updater.installDirectory = path.win32.dirname(executablePath)
  }
  updater.quitAndInstall(isWindows, true)
}
