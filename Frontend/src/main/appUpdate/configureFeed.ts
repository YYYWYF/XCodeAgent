import type { AppUpdater } from 'electron-updater'

/** 先加载开发缓存配置，再设置运行时源，避免 SDK 的路径 setter 清除 Java 返回的 CDN。 */
export function configureUpdateFeed(
  updater: Pick<AppUpdater, 'updateConfigPath' | 'setFeedURL'>,
  baseUrl: string,
  developmentConfigPath?: string
): void {
  if (developmentConfigPath) updater.updateConfigPath = developmentConfigPath
  updater.setFeedURL({ provider: 'generic', url: baseUrl })
}
