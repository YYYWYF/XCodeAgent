import type { BackendConnectionState } from '../shared/backendConnection'

type BackendIdentity = Pick<BackendConnectionState, 'baseUrl' | 'backendInstanceId'>

/** 主进程统一监督连接：合并并发恢复，停止后的旧探测和旧启动结果不能重新开放服务。 */
export class BackendConnectionMonitor {
  private state: BackendConnectionState
  private listeners = new Set<(state: BackendConnectionState) => void>()
  private timer?: ReturnType<typeof setTimeout>
  private pending?: Promise<void>
  private generation = 0
  private running = false
  private failures = 0

  /** 注入身份探测和安全启动，监督器不持有任务请求或任何业务重放能力。 */
  constructor(private options: { baseUrl: string; probe: () => Promise<BackendIdentity | undefined>;
    recover: () => Promise<BackendIdentity>; intervalMs?: number }) {
    this.state = { revision: 0, status: 'reconnecting', baseUrl: options.baseUrl }
  }

  /** 只向桌面公开连接状态和地址，不公开控制凭据或后台内部状态。 */
  getState(): BackendConnectionState { return this.state }

  /** 订阅已完成的连接变更。 */
  subscribe(listener: (state: BackendConnectionState) => void): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  /** 只在状态或实例变化时增加代次，健康轮询不引发界面重复同步。 */
  private publish(status: BackendConnectionState['status'], identity?: BackendIdentity): void {
    const next = { ...this.state, ...identity, status }
    if (next.status === this.state.status && next.baseUrl === this.state.baseUrl &&
      next.backendInstanceId === this.state.backendInstanceId) return
    this.state = { ...next, revision: this.state.revision + 1 }
    this.listeners.forEach((listener) => listener(this.state))
  }

  /** 初始启动和显式接管完成后复用同一连接投影。 */
  ready(identity: BackendIdentity): void {
    this.failures = 0
    this.publish('ready', identity)
  }

  /** 启用串行监督，定时器不阻止 Electron 退出。 */
  start(): void {
    if (this.running) return
    this.running = true
    this.generation++
    this.schedule()
  }

  /** 退出和安装前撤销监督代次，迟到探测不能再触发启动。 */
  stop(): void {
    this.running = false
    this.generation++
    clearTimeout(this.timer)
    this.publish('stopped')
  }

  /** 健康时十五秒巡检；首次失败三秒复查，持续断连退避至三十秒，不叠加请求。 */
  private schedule(): void {
    if (!this.running) return
    const interval = this.failures || this.state.status !== 'ready'
      ? Math.min(30000, 3000 * 2 ** Math.min(Math.max(0, this.failures - 1), 4))
      : 15000
    this.timer = setTimeout(() => void this.checkNow(), this.options.intervalMs ?? interval)
    this.timer.unref()
  }

  /** 短暂探测失败先复查，确认进程退出可立即恢复；同次断连不反复切换传输代次。 */
  checkNow(confirmedExit = false): Promise<void> {
    if (!this.running) return Promise.resolve()
    if (this.pending) return this.pending
    clearTimeout(this.timer)
    const generation = this.generation
    const current = (): boolean => this.running && this.generation === generation
    const pending = Promise.resolve().then(async () => {
      try {
        // 超时和异常都需连续失败确认，单次迟到不能撤销仍在正常输出的 AG-UI 订阅。
        const healthy = await this.options.probe().catch(() => undefined)
        if (!current()) return
        if (healthy) { this.ready(healthy); return }
        this.failures++
        if (!confirmedExit && this.state.status === 'ready' && this.failures < 2) return
        // 已不可用时只在后台重试；不能每轮 unavailable → reconnecting 都使 Renderer 失效。
        if (this.state.status !== 'unavailable') this.publish('reconnecting')
        const restored = await this.options.recover()
        if (current()) this.ready(restored)
      } catch {
        if (current()) this.publish('unavailable')
      } finally {
        if (this.pending === pending) this.pending = undefined
        if (this.running) this.schedule()
      }
    })
    this.pending = pending
    return pending
  }
}
