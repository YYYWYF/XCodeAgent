import assert from 'node:assert/strict'
import { test } from 'node:test'
import electronUpdater from 'electron-updater'
import type { NsisUpdater } from 'electron-updater'
import type { InstallOptions } from 'electron-updater/out/BaseUpdater'
import { quitAndInstallUpdate } from '../src/main/appUpdate/installUpdate'

/** 使用真实 NSIS 参数生成逻辑，拦截进程启动以避免执行安装器。 */
class InspectableInstaller extends electronUpdater.NsisUpdater {
  readonly launches: { executable: string; args: string[] }[] = []

  /** 保留 SDK 的安装参数转换，跳过依赖 Electron 运行时的退出动作。 */
  override quitAndInstall(isSilent = false, isForceRunAfter = false): void {
    const options: InstallOptions = {
      installerPath: 'C:\\Update Cache\\setup.exe',
      isSilent,
      isForceRunAfter,
      isAdminRightsRequired: false
    }
    this.doInstall(options)
  }

  /** 捕获传给安装器的真实 argv，验证空格和中文路径没有被拆分。 */
  override async spawnLog(executable: string, args: string[] = []): Promise<boolean> {
    this.launches.push({ executable, args })
    return true
  }
}

test('Windows 更新静默安装到当前 EXE 目录，并要求安装完成后重启', () => {
  for (const directory of [
    'D:\\公司应用\\DevAgent Studio',
    'C:\\Program Files\\DevAgent Studio',
    'C:\\Users\\tester\\AppData\\Local\\Programs\\devagentstudio'
  ]) {
    const updater = new InspectableInstaller(null, {
      version: '1.0.0',
      name: 'install-test',
      isPackaged: true,
      appUpdateConfigPath: '/unused/app-update.yml',
      userDataPath: '/unused/user-data',
      baseCachePath: '/unused/cache',
      whenReady: async () => undefined,
      relaunch: () => undefined,
      quit: () => undefined,
      onQuit: () => undefined
    })
    quitAndInstallUpdate(updater, 'win32', `${directory}\\devagentstudio.exe`)
    assert.deepEqual(updater.launches, [
      {
        executable: 'C:\\Update Cache\\setup.exe',
        args: ['--updated', '/S', '--force-run', `/D=${directory}`]
      }
    ])
  }
})

test('macOS 安装保持原生调用，不设置 Windows 安装目录', () => {
  const calls: boolean[][] = []
  const updater: Pick<NsisUpdater, 'installDirectory' | 'quitAndInstall'> = {
    /** 捕获 macOS 的交接参数，不启动原生安装器。 */
    quitAndInstall: (isSilent = false, isForceRunAfter = false): void => {
      calls.push([isSilent, isForceRunAfter])
    }
  }
  quitAndInstallUpdate(
    updater,
    'darwin',
    '/Applications/DevAgent Studio.app/Contents/MacOS/DevAgent Studio'
  )
  assert.deepEqual(calls, [[false, true]])
  assert.equal(updater.installDirectory, undefined)
})
