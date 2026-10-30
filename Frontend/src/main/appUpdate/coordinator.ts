import type { AppUpdateState } from '../../shared/appUpdate'

export type ReleaseCheck = {
  updateAvailable: boolean
  latestAppVersion: string
  appDownloadUrl: string | null
}
export type UpdateProgress = { percent: number; transferred: number; total: number }
export type UpdateDriver = {
  check: () => Promise<{ version: string } | null>
  download: (signal: AbortSignal, progress: (value: UpdateProgress) => void) => Promise<void>
  install: () => Promise<void>
  dispose: () => void
}
type Timings = { check: number; manifest: number; idle: number; download: number; notice: number }
type Options = {
  currentVersion: string
  isPackaged: boolean
  prepare?: () => Promise<void>
  check: (signal: AbortSignal) => Promise<ReleaseCheck>
  createDriver: (url: string) => UpdateDriver
  publish: (state: AppUpdateState) => void
  prepareInstall: () => Promise<void>
  timings?: Partial<Timings>
}

/** 对启动更新进行一次性编排，失败放行后不再消费迟到结果。 */
export class AppUpdateCoordinator {
  private state: AppUpdateState
  private driver?: UpdateDriver
  private readonly cancellation = new AbortController()
  private readonly timings: Timings
  private readonly timers = new Set<ReturnType<typeof setTimeout>>()
  private started = false
  private finished = false
  private resolveResult!: (value: boolean) => void
  readonly result = new Promise<boolean>((resolve) => {
    this.resolveResult = resolve
  })

  constructor(private readonly options: Options) {
    this.timings = {
      check: 5000,
      manifest: 10000,
      idle: 30000,
      download: 900000,
      notice: 1500,
      ...options.timings
    }
    this.state = {
      revision: 0,
      phase: 'checking',
      currentVersion: options.currentVersion,
      isPackaged: options.isPackaged,
      percent: 0,
      transferred: 0,
      total: 0,
      message: options.prepare ? '正在启动本地服务' : '正在检测更新'
    }
  }

  /** 返回只读快照，供渲染进程补取首次事件。 */
  snapshot(): AppUpdateState {
    return { ...this.state }
  }

  /** 冷启动只执行一次，明确允许更新后才读取清单与下载。 */
  async start(): Promise<void> {
    if (this.started || this.finished) return
    this.started = true
    // Python 启动沿用原有就绪预算，失败交回应用启动处理，不计入五秒版本检测。
    if (this.options.prepare) {
      await this.options.prepare()
      if (this.finished) return
      this.update({ message: '正在检测更新' })
    }
    try {
      const release = await this.deadline(
        this.options.check(this.cancellation.signal),
        this.timings.check,
        '版本检测超时'
      )
      if (this.finished) return
      if (!release.updateAvailable) return this.finish(true)
      this.update({ latestAppVersion: release.latestAppVersion })
      if (!release.appDownloadUrl) throw new Error('更新地址缺失')
      this.driver = this.options.createDriver(release.appDownloadUrl)
      const manifest = await this.deadline(
        this.driver.check(),
        this.timings.manifest,
        '更新清单读取超时'
      )
      if (this.finished) return
      if (!manifest) throw new Error(`未获取可用更新清单（服务端版本 ${release.latestAppVersion}）`)
      if (manifest.version !== release.latestAppVersion)
        throw new Error(
          `更新清单版本 ${manifest.version} 与服务端版本 ${release.latestAppVersion} 不一致`
        )
      this.update({ phase: 'downloading', message: '正在下载新版本' })
      await this.download()
      if (!this.finished)
        this.update({ phase: 'downloaded', percent: 100, message: '新版本已下载完成' })
    } catch (error) {
      this.fail(error)
    }
  }

  /** 仅已下载的打包应用允许安装，清理完成后再交接系统安装器。 */
  async install(): Promise<void> {
    if (
      this.finished ||
      this.state.phase !== 'downloaded' ||
      !this.options.isPackaged ||
      !this.driver
    )
      return
    this.update({ phase: 'installing', message: '正在准备安装，请稍候' })
    try {
      await this.options.prepareInstall()
      if (this.finished) return
      await this.driver.install()
      this.finish(false)
    } catch (error) {
      this.fail(error)
    }
  }

  /** 源码运行不能替换 Electron 开发程序，只允许返回原启动流程。 */
  continueDevelopment(): void {
    if (!this.options.isPackaged && this.state.phase === 'downloaded') this.finish(true)
  }

  /** 退出整个应用，不触发已下载更新的安装。 */
  quit(): void {
    this.finish(false)
  }

  /** 发布递增快照，避免首次读取覆盖已经收到的较新事件。 */
  private update(patch: Partial<AppUpdateState>): void {
    if (this.finished) return
    this.state = { ...this.state, ...patch, revision: this.state.revision + 1 }
    this.options.publish(this.snapshot())
  }

  /** 同时限制下载总耗时与无新增数据时长，取消后不再更新界面。 */
  private async download(): Promise<void> {
    const driver = this.driver!
    let idleTimer: ReturnType<typeof setTimeout> | undefined
    let lastTransferred = -1
    const downloading = new Promise<void>((resolve, reject) => {
      /** 重置无进度计时器，重复进度事件不延长等待。 */
      const resetIdle = (): void => {
        this.clearTimer(idleTimer)
        idleTimer = this.setTimer(() => reject(new Error('下载长时间无进度')), this.timings.idle)
      }
      resetIdle()
      driver
        .download(this.cancellation.signal, (progress) => {
          if (this.finished || this.cancellation.signal.aborted) return
          if (progress.transferred > lastTransferred) {
            lastTransferred = progress.transferred
            resetIdle()
          }
          this.update({ ...progress, percent: Math.max(0, Math.min(100, progress.percent)) })
        })
        .then(resolve, reject)
    })
    try {
      await this.deadline(downloading, this.timings.download, '下载耗时过长')
    } finally {
      this.clearTimer(idleTimer)
    }
  }

  /** 失败立即取消更新，短暂显示原因后自动放行登录。 */
  private fail(error: unknown): void {
    if (this.finished) return
    this.cancellation.abort()
    this.driver?.dispose()
    this.update({
      phase: 'error',
      message: `${error instanceof Error ? error.message : '更新失败'}，即将打开应用`
    })
    this.setTimer(() => this.finish(true), this.timings.notice)
  }

  /** 终态只提交一次；无论超时、退出还是继续都取消残留下载。 */
  private finish(continueStartup: boolean): void {
    if (this.finished) return
    this.update({ phase: continueStartup ? 'continuing' : 'quitting' })
    this.finished = true
    this.cancellation.abort()
    this.driver?.dispose()
    for (const timer of this.timers) clearTimeout(timer)
    this.timers.clear()
    this.resolveResult(continueStartup)
  }

  /** 将不可取消的清单请求与有界等待隔离，迟到结果不能推进状态。 */
  private async deadline<T>(work: Promise<T>, milliseconds: number, message: string): Promise<T> {
    let timer: ReturnType<typeof setTimeout> | undefined
    try {
      return await Promise.race([
        work,
        new Promise<never>((_resolve, reject) => {
          timer = this.setTimer(() => reject(new Error(message)), milliseconds)
        })
      ])
    } finally {
      this.clearTimer(timer)
    }
  }

  /** 统一登记启动阶段定时器，终态能够集中回收。 */
  private setTimer(callback: () => void, milliseconds: number): ReturnType<typeof setTimeout> {
    const timer = setTimeout(() => {
      this.timers.delete(timer)
      callback()
    }, milliseconds)
    this.timers.add(timer)
    return timer
  }

  /** 删除已结束或被替换的单个定时器。 */
  private clearTimer(timer: ReturnType<typeof setTimeout> | undefined): void {
    if (timer !== undefined) {
      clearTimeout(timer)
      this.timers.delete(timer)
    }
  }
}
