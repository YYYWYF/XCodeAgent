import type { BackendConnectionState } from '../shared/backendConnection'

type BackendIdentity = Pick<BackendConnectionState, 'baseUrl' | 'backendInstanceId'>

/** 仅按明确事件合并连接恢复；不定时探测后台，旧启动结果不能重新开放服务。 */
export class BackendConnectionMonitor {
  private state: BackendConnectionState
  private listeners = new Set<(state: BackendConnectionState) => void>()
  private pending?: Promise<void>
  private generation = 0
  private running = false

  /** 注入身份探测和安全启动，监督器不持有任务请求或任何业务重放能力。 */
  constructor(private options: { baseUrl: string; probe: () => Promise<BackendIdentity | undefined>;
    recover: () => Promise<BackendIdentity> }) {
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
    this.publish('ready', identity)
  }

  /** 允许处理后续进程事件，不创建后台健康检查定时器。 */
  start(): void {
    if (this.running) return
    this.running = true
    this.generation++
  }

  /** 退出和安装前撤销监督代次，迟到探测不能再触发启动。 */
  stop(): void {
    this.running = false
    this.generation++
    this.publish('stopped')
  }

  /** 已知进程退出时核验并安全恢复一次；失败后不自行重试。 */
  checkNow(): Promise<void> {
    if (!this.running) return Promise.resolve()
    if (this.pending) return this.pending
    const generation = this.generation
    const current = (): boolean => this.running && this.generation === generation
    const pending = Promise.resolve().then(async () => {
      try {
        const healthy = await this.options.probe().catch(() => undefined)
        if (!current()) return
        if (healthy) { this.ready(healthy); return }
        // 明确事件再次处理同次失败时，不额外切换代次撤销 Renderer 的订阅。
        if (this.state.status !== 'unavailable') this.publish('reconnecting')
        const restored = await this.options.recover()
        if (current()) this.ready(restored)
      } catch {
        if (current()) this.publish('unavailable')
      } finally {
        if (this.pending === pending) this.pending = undefined
      }
    })
    this.pending = pending
    return pending
  }
}
