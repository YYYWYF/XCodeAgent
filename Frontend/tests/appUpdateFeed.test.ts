import assert from 'node:assert/strict'
import { test } from 'node:test'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import type { RequestOptions } from 'node:http'
import electronUpdater from 'electron-updater'
import { configureUpdateFeed } from '../src/main/appUpdate/configureFeed'

/** 使用项目实际 updater SDK 检查源解析，不启动原生安装器。 */
class InspectableUpdater extends electronUpdater.NsisUpdater {
  /** 暴露 SDK 最终解析的文件地址，以验证运行时配置不会退回开发文件。 */
  async resolvedFiles(): Promise<string[]> {
    const { info, provider } = await this.getUpdateInfoAndProvider()
    return provider.resolveFiles(info).map((file) => file.url.href)
  }
}

test('SDK 开发配置路径重置后，仍从 Java 返回的 CDN 读取清单和安装包', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'app-update-feed-'))
  try {
    const config = path.join(directory, 'dev-app-update.yml')
    await fs.writeFile(
      config,
      'provider: generic\nurl: http://stale-dev-source.example/\nupdaterCacheDirName: feed-test\n'
    )
    const updater = new InspectableUpdater(
      { provider: 'generic', url: 'http://java-cdn.example/releases/' },
      {
        version: '1.0.0',
        name: 'feed-test',
        isPackaged: false,
        appUpdateConfigPath: config,
        userDataPath: directory,
        baseCachePath: directory,
        whenReady: async () => undefined,
        relaunch: () => undefined,
        quit: () => undefined,
        onQuit: () => undefined
      }
    )
    updater.logger = { info: () => undefined, warn: () => undefined, error: () => undefined }
    const requested: string[] = []
    Object.defineProperty(updater, 'httpExecutor', {
      value: {
        /** 捕获 SDK 真正请求的地址，并返回有效的发布清单。 */
        request: async (options: RequestOptions): Promise<string> => {
          requested.push(`${options.protocol}//${options.hostname}${options.path}`)
          return JSON.stringify({
            version: '1.0.1',
            files: [{ url: 'app-setup.exe', sha512: Buffer.alloc(64).toString('base64'), size: 10 }]
          })
        }
      }
    })
    configureUpdateFeed(updater, 'http://java-cdn.example/releases/', config)
    const files = await updater.resolvedFiles()
    assert.equal(requested.length, 1)
    assert.equal(new URL(requested[0]).origin, 'http://java-cdn.example')
    assert.ok(new URL(requested[0]).pathname.startsWith('/releases/latest'))
    assert.deepEqual(files, ['http://java-cdn.example/releases/app-setup.exe'])
  } finally {
    await fs.rm(directory, { force: true, recursive: true })
  }
})
