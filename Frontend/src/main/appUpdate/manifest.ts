import type { UpdateInfo } from 'electron-updater'

/** 检查当前平台存在匹配的安装文件，避免 Mac 缺少架构包时意外选错包。 */
export function assertUpdateFiles(info: UpdateInfo, platform: string, arch: string): void {
  const files = info.files.filter((file) => {
    const url = new URL(file.url, 'https://update.invalid/')
    if (!['http:', 'https:'].includes(url.protocol)) return false
    if (platform === 'win32') return url.pathname.toLowerCase().endsWith('.exe')
    if (!url.pathname.toLowerCase().endsWith('.zip')) return false
    return arch === 'arm64' ? url.pathname.includes('arm64') : !url.pathname.includes('arm64')
  })
  if (!files.length) throw new Error('更新清单缺少当前系统或架构的安装包')
  for (const file of files) {
    if (
      typeof file.sha512 !== 'string' ||
      Buffer.from(file.sha512, 'base64').length !== 64 ||
      !Number.isSafeInteger(file.size) ||
      file.size! <= 0
    )
      throw new Error('更新清单缺少有效文件大小或摘要')
  }
}
