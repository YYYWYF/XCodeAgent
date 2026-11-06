import { isAgentProjectPage, type DesignPreviewPage } from '../components/DesignRenderer/projectPreview'

export type DesignPageSource = DesignPreviewPage
export type DesignSourceSnapshot = {
  codes: Record<string, string>
  statuses: Record<string, string>
  projects?: Record<string, DesignPageSource>
}
export const EMPTY_DESIGN_SOURCE: DesignSourceSnapshot = { codes: {}, statuses: {} }
const sources = new Map<string, UiDesignPageSource>()
const readsInFlight = new Map<string, Promise<DesignPageSource[] | undefined>>()

/** 视图隐藏后马上重新显示时也复用尚未完成的 IPC，避免旧读取与新订阅重叠。 */
function readDesignPages(workspace: string): Promise<DesignPageSource[] | undefined> {
  const existing = readsInFlight.get(workspace)
  if (existing) return existing
  const reading = Promise.resolve()
    .then(async () => {
      const result = await window.devAgentStudio?.workspace?.readUiDesigns({
        workspaceRoot: workspace
      })
      const pages = (result?.uiDesigns as { pages?: DesignPageSource[] } | undefined)?.pages
      return Array.isArray(pages) ? pages : undefined
    })
    .finally(() => {
      if (readsInFlight.get(workspace) === reading) readsInFlight.delete(workspace)
    })
  readsInFlight.set(workspace, reading)
  return reading
}

/** 比较磁盘投影，未变化时保持对象身份，避免重复渲染和源码复制。 */
function sameRecord(left: Record<string, string>, right: Record<string, string>): boolean {
  const keys = Object.keys(left)
  return keys.length === Object.keys(right).length && keys.every((key) => left[key] === right[key])
}

/** 判断后台生成池是否仍有待领取或运行中的页面。 */
export function designPagesGenerating(pages: DesignPageSource[]): boolean {
  return pages.some((page) => page.status === 'queued' || page.status === 'generating')
}

/** 按工作区共享磁盘读取；只有生成期间才串行轮询，不拥有或取消后台任务。 */
export class UiDesignPageSource {
  snapshot = EMPTY_DESIGN_SOURCE
  private listeners = new Set<(value: DesignSourceSnapshot) => void>()
  private timer?: ReturnType<typeof setTimeout>
  private inFlight = false
  private refreshPending = false
  private generating = false
  private sourceKey = ''
  private disposed = false

  /** 注入读取与可见性，便于测试慢请求、后台完成和隐藏窗口边界。 */
  constructor(
    private readonly read: () => Promise<DesignPageSource[] | undefined>,
    private readonly visible: () => boolean,
    private readonly interval = 2000
  ) {}

  /** 注册展示订阅，立即复用同工作区已有投影。 */
  subscribe(listener: (value: DesignSourceSnapshot) => void): () => void {
    this.listeners.add(listener)
    listener(this.snapshot)
    return () => {
      this.listeners.delete(listener)
    }
  }

  /** 后端事件中的页面状态变化触发一次读取，不因普通 React 渲染重复请求。 */
  updateSource(key: string, generating: boolean): void {
    if (this.sourceKey === key) return
    this.sourceKey = key
    this.generating = generating
    this.refresh()
  }

  /** 恢复可见或收到新生成信号时立即校准；慢请求期间最多合并一次后续读取。 */
  refresh(): void {
    clearTimeout(this.timer)
    if (this.disposed || !this.visible()) return
    if (this.inFlight) {
      this.refreshPending = true
      return
    }
    void this.poll()
  }

  /** 隐藏只停止展示轮询，不触碰任何生成任务、消息流或执行登记。 */
  pause(): void {
    clearTimeout(this.timer)
  }

  /** 最后一个展示订阅离开时丢弃源码引用，并隔离迟到的磁盘回执。 */
  dispose(): void {
    this.disposed = true
    this.pause()
    this.listeners.clear()
    this.snapshot = EMPTY_DESIGN_SOURCE
  }

  /** 等当前读取完成才安排下一轮；稳定磁盘终态覆盖快照中残留的 generating。 */
  private async poll(): Promise<void> {
    this.inFlight = true
    try {
      const pages = await this.read()
      if (this.disposed) return
      if (pages) {
        // 请求期间若来了新一轮生成信号，旧磁盘结果不能终止新一轮跟进。
        if (!this.refreshPending) this.generating = designPagesGenerating(pages)
        const next: DesignSourceSnapshot = { codes: {}, statuses: {} }
        for (const page of pages) {
          if (!page.pageId) continue
          if (page.code) next.codes[page.pageId] = page.code
          if (page.status) next.statuses[page.pageId] = page.status
          if (isAgentProjectPage(page)) {
            next.projects ??= {}
            next.projects[page.pageId] = page
          }
        }
        if (
          !sameRecord(next.codes, this.snapshot.codes) ||
          !sameRecord(next.statuses, this.snapshot.statuses) ||
          JSON.stringify(next.projects) !== JSON.stringify(this.snapshot.projects)
        ) {
          this.snapshot = next
          this.listeners.forEach((listener) => listener(next))
        }
      }
    } catch {
      // 读取故障仅在已知仍在生成时重试；闲置视图等待重新显示或显式同步。
    } finally {
      this.inFlight = false
      if (!this.disposed && this.visible()) {
        if (this.refreshPending) {
          this.refreshPending = false
          this.refresh()
        } else if (this.generating) {
          this.timer = setTimeout(() => this.refresh(), this.interval)
        }
      }
    }
  }
}

/** 同一工作区多个设计稿组件共用一个读取器；无订阅时不保留历史源码。 */
export function subscribeDesignSource(
  workspace: string,
  listener: (value: DesignSourceSnapshot) => void
): { source: UiDesignPageSource; release: () => void } {
  let source = sources.get(workspace)
  if (!source) {
    source = new UiDesignPageSource(
      () => readDesignPages(workspace),
      () => document.visibilityState !== 'hidden'
    )
    sources.set(workspace, source)
  }
  const entry = source
  const unsubscribe = entry.subscribe(listener)
  // 共享条目的订阅计数独立于源码内容，避免把空清单误当作未加载。
  const count = subscriptions.get(workspace) || 0
  subscriptions.set(workspace, count + 1)
  /** 隐藏窗口时暂停，重新可见或聚焦时重新读取一次。 */
  const onVisible = (): void => {
    if (document.visibilityState === 'hidden') entry.pause()
    else entry.refresh()
  }
  if (count === 0) {
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('focus', onVisible)
    visibilityCleanups.set(workspace, () => {
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('focus', onVisible)
    })
  }
  return {
    source: entry,
    release: () => {
      unsubscribe()
      const remaining = (subscriptions.get(workspace) || 1) - 1
      if (remaining) subscriptions.set(workspace, remaining)
      else {
        entry.dispose()
        sources.delete(workspace)
        subscriptions.delete(workspace)
        visibilityCleanups.get(workspace)?.()
        visibilityCleanups.delete(workspace)
      }
    }
  }
}

const subscriptions = new Map<string, number>()
const visibilityCleanups = new Map<string, () => void>()
