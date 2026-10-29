import assert from 'node:assert/strict'
import './appUpdateProtocol.test'
import './appUpdateFeed.test'
import './appUpdateInstall.test'
import { test } from 'node:test'
import fs from 'node:fs/promises'
import path from 'node:path'
import os from 'node:os'
import { createHash } from 'node:crypto'
import type { AddressInfo } from 'node:net'
import type { UpdateInfo } from 'electron-updater'
import {
  AppUpdateCoordinator,
  type UpdateDriver,
  type ReleaseCheck
} from '../src/main/appUpdate/coordinator'
import { parseReleaseCheck, checkAppRelease } from '../src/main/appUpdate/checkRelease'
import { assertUpdateFiles } from '../src/main/appUpdate/manifest'
import { prepareCdn, createCdnServer, parseRange } from '../scripts/app-update-cdn.mjs'

const release: ReleaseCheck = {
  updateAvailable: true,
  latestAppVersion: '1.0.1',
  appDownloadUrl: 'http://127.0.0.1:18081/'
}

/** 创建可控制的异步任务，用于验证退出与迟到响应的竞态。 */
function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

/** 建立纯协调器测试环境，不依赖 Electron 或真实安装器。 */
function fixture(
  overrides: Partial<UpdateDriver> = {},
  packaged = false,
  check = async (): Promise<ReleaseCheck> => release
): {
  coordinator: AppUpdateCoordinator
  events: string[]
  installs: () => number
  downloads: () => number
} {
  const events: string[] = []
  let installs = 0
  let downloads = 0
  const driver: UpdateDriver = {
    check: async () => ({ version: '1.0.1' }),
    download: async (_signal, progress) => {
      downloads++
      progress({ percent: 100, transferred: 10, total: 10 })
    },
    install: async () => {
      events.push('install')
      installs++
    },
    dispose: () => {
      events.push('dispose')
    },
    ...overrides
  }
  const coordinator = new AppUpdateCoordinator({
    currentVersion: '1.0.0',
    isPackaged: packaged,
    check,
    createDriver: () => driver,
    publish: (state) => {
      events.push(state.phase)
    },
    prepareInstall: async () => {
      events.push('cleanup')
    },
    timings: { notice: 1, check: 20, manifest: 20, idle: 20, download: 80 }
  })
  return { coordinator, events, installs: () => installs, downloads: () => downloads }
}

test('无更新直接放行，检测只执行一次', async () => {
  let calls = 0
  const f = fixture({}, false, async () => {
    calls++
    return { ...release, updateAvailable: false, appDownloadUrl: null }
  })
  await Promise.all([f.coordinator.start(), f.coordinator.start()])
  assert.equal(await f.coordinator.result, true)
  assert.equal(calls, 1)
  assert.equal(f.downloads(), 0)
})

test('检测错误与超时均自动放行', async () => {
  for (const check of [
    async (): Promise<ReleaseCheck> => {
      throw new Error('offline')
    },
    (): Promise<ReleaseCheck> => new Promise(() => undefined)
  ]) {
    const f = fixture({}, false, check)
    await f.coordinator.start()
    assert.equal(await f.coordinator.result, true)
    assert.ok(f.events.includes('error'))
  }
})

test('清单不一致及清单超时不会下载', async () => {
  for (const check of [
    async () => ({ version: '1.2.0' }),
    () => new Promise<null>(() => undefined)
  ]) {
    const f = fixture({ check })
    await f.coordinator.start()
    assert.equal(await f.coordinator.result, true)
    assert.equal(f.downloads(), 0)
  }
})

test('版本不一致提示包含 Java 和 CDN 的实际版本', async () => {
  const f = fixture({ check: async () => ({ version: '1.2.0' }) })
  await f.coordinator.start()
  assert.equal(await f.coordinator.result, true)
  assert.match(f.coordinator.snapshot().message, /1\.2\.0.*1\.0\.1/)
  assert.equal(f.downloads(), 0)
})

test('源码下载完成后必须显式继续，且禁止执行安装', async () => {
  const f = fixture()
  await f.coordinator.start()
  assert.equal(f.coordinator.snapshot().phase, 'downloaded')
  assert.equal(f.coordinator.snapshot().percent, 100)
  await f.coordinator.install()
  assert.equal(f.installs(), 0)
  f.coordinator.continueDevelopment()
  assert.equal(await f.coordinator.result, true)
})

test('打包应用下载完成后退出不安装，也不能使用开发继续入口', async () => {
  const f = fixture({}, true)
  await f.coordinator.start()
  f.coordinator.continueDevelopment()
  assert.equal(f.coordinator.snapshot().phase, 'downloaded')
  f.coordinator.quit()
  assert.equal(await f.coordinator.result, false)
  assert.equal(f.installs(), 0)
})

test('安装只触发一次，且退出清理先于安装器', async () => {
  const f = fixture({}, true)
  await f.coordinator.start()
  await Promise.all([f.coordinator.install(), f.coordinator.install()])
  assert.equal(await f.coordinator.result, false)
  assert.equal(f.installs(), 1)
  assert.ok(f.events.indexOf('cleanup') < f.events.indexOf('install'))
})

test('安装交接前失败恢复原启动流程', async () => {
  const f = fixture(
    {
      install: async () => {
        throw new Error('签名无效')
      }
    },
    true
  )
  await f.coordinator.start()
  await f.coordinator.install()
  assert.equal(await f.coordinator.result, true)
})

test('校验失败、磁盘错误及无进度超时取消下载后放行', async () => {
  for (const download of [
    async (): Promise<void> => {
      throw new Error('SHA-512 mismatch')
    },
    async (): Promise<void> => {
      throw new Error('ENOSPC')
    },
    (): Promise<void> => new Promise(() => undefined)
  ]) {
    const f = fixture({ download })
    await f.coordinator.start()
    assert.equal(await f.coordinator.result, true)
    assert.ok(f.events.includes('error'))
  }
})

test('退出后迟到的检测与下载结果不能重新打开界面', async () => {
  const checking = deferred<ReleaseCheck>()
  const f = fixture({}, false, () => checking.promise)
  const start = f.coordinator.start()
  f.coordinator.quit()
  const revision = f.coordinator.snapshot().revision
  checking.resolve(release)
  await start
  assert.equal(f.coordinator.snapshot().revision, revision)
  assert.equal(f.downloads(), 0)

  const downloading = deferred<void>()
  const g = fixture({ download: () => downloading.promise })
  const second = g.coordinator.start()
  await new Promise((resolve) => setTimeout(resolve, 1))
  g.coordinator.quit()
  downloading.resolve()
  await second
  assert.equal(g.coordinator.snapshot().phase, 'quitting')
})

test('持续进度仍受总下载时限约束，超时会取消下载', async () => {
  let aborted = false
  const f = fixture({
    download: (signal, progress) =>
      new Promise<void>((resolve) => {
        let transferred = 0
        const timer = setInterval(() => {
          transferred++
          progress({ percent: 1, transferred, total: 1000 })
        }, 5)
        signal.addEventListener(
          'abort',
          () => {
            aborted = true
            clearInterval(timer)
            resolve()
          },
          { once: true }
        )
      })
  })
  await f.coordinator.start()
  assert.equal(await f.coordinator.result, true)
  assert.equal(aborted, true)
  assert.ok(f.events.includes('error'))
})

test('Java 响应严格校验并支持内网 HTTP 基础地址', () => {
  assert.deepEqual(parseReleaseCheck(release), release)
  for (const value of [
    null,
    [],
    {},
    { ...release, updateAvailable: 'true' },
    { ...release, appDownloadUrl: null },
    { ...release, appDownloadUrl: 'file:///app.exe' },
    { ...release, updateAvailable: false }
  ])
    assert.throws(() => parseReleaseCheck(value))
})

test('清单区分 Mac 架构、Windows 格式，并拒绝缺失摘要', () => {
  const info: UpdateInfo = {
    version: '1.0.1',
    releaseDate: '2026-10-28',
    path: '',
    sha512: '',
    files: [{ url: 'app-arm64-mac.zip', sha512: Buffer.alloc(64).toString('base64'), size: 10 }]
  }
  assert.doesNotThrow(() => assertUpdateFiles(info, 'darwin', 'arm64'))
  assert.throws(() => assertUpdateFiles(info, 'darwin', 'x64'))
  assert.throws(() => assertUpdateFiles(info, 'win32', 'x64'))
  const win = { ...info, files: [{ ...info.files[0], url: 'app-setup.exe' }] }
  assert.doesNotThrow(() => assertUpdateFiles(win, 'win32', 'x64'))
  assert.throws(() =>
    assertUpdateFiles({ ...win, files: [{ ...win.files[0], sha512: '' }] }, 'win32', 'x64')
  )
})

test('Range 支持前缀、后缀及越界拒绝', () => {
  assert.deepEqual(parseRange('bytes=2-5', 10), { start: 2, end: 5 })
  assert.deepEqual(parseRange('bytes=2-', 10), { start: 2, end: 9 })
  assert.deepEqual(parseRange('bytes=-3', 10), { start: 7, end: 9 })
  for (const value of ['bytes=10-', 'bytes=5-2', 'bytes=-0', 'bytes=0-1,3-4', 'invalid'])
    assert.equal(parseRange(value, 10), null)
})

test('本地 CDN 真实传输、空格文件名、摘要检查及文件白名单', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'cdn-test-'))
  const bytes = Buffer.from('test installer bytes')
  const filename = 'DevAgent Studio-arm64-mac.zip'
  const sha512 = createHash('sha512').update(bytes).digest('base64')
  try {
    await fs.writeFile(path.join(directory, filename), bytes)
    await fs.writeFile(
      path.join(directory, 'latest-mac.yml'),
      JSON.stringify({ version: '1.0.1', files: [{ url: filename, sha512, size: bytes.length }] })
    )
    await fs.writeFile(path.join(directory, '.env'), 'not published')
    const prepared = await prepareCdn(directory)
    const server = createCdnServer(prepared)
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    const base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`
    try {
      const response = await fetch(`${base}/${encodeURIComponent(filename)}`)
      assert.deepEqual(Buffer.from(await response.arrayBuffer()), bytes)
      assert.equal((await fetch(`${base}/.env`)).status, 404)
      assert.equal((await fetch(`${base}/%2e%2e/.env`)).status, 404)
      const head = await fetch(`${base}/${encodeURIComponent(filename)}`, { method: 'HEAD' })
      assert.equal(head.headers.get('content-length'), String(bytes.length))
      assert.equal((await head.arrayBuffer()).byteLength, 0)
      const part = await fetch(`${base}/${encodeURIComponent(filename)}`, {
        headers: { Range: 'bytes=2-5' }
      })
      assert.equal(part.status, 206)
      assert.deepEqual(Buffer.from(await part.arrayBuffer()), bytes.subarray(2, 6))
      assert.equal(
        (
          await fetch(`${base}/${encodeURIComponent(filename)}`, {
            headers: { Range: 'bytes=999-' }
          })
        ).status,
        416
      )
      await assert.rejects(
        checkAppRelease(`${base}/missing`, '1.0.0', new AbortController().signal)
      )
    } finally {
      server.closeAllConnections()
      await new Promise<void>((resolve) => server.close(() => resolve()))
    }
    await fs.writeFile(path.join(directory, filename), Buffer.alloc(bytes.length))
    await assert.rejects(prepareCdn(directory), /SHA-512/)
  } finally {
    await fs.rm(directory, { force: true, recursive: true })
  }
})
