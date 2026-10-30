import { app, BrowserWindow } from 'electron'
import { createHash, randomUUID } from 'node:crypto'
import type { AgentSubscriber } from '@ag-ui/client'
import verifierSource from './runtimeAcceptanceWorker.ts?raw'

interface AcceptanceJob {
  token: string; mode: 'endpoint' | 'page'; runtimeUrl: string; pageUrl: string
  agentId: string; threadId: string; runId: string; question: string
}
interface Evidence {
  passed: boolean; agentId: string; threadId: string; runId: string; lifecycle: boolean
  textSha256: string; textBytes: number; pageObserved: boolean; reason: string
}

/** 只允许 Studio 签发的本地服务地址，禁止验收桥跳转到外部网络或文件。 */
function localUrl(raw: string): string {
  const url = new URL(raw)
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(url.hostname)
    || !url.port || url.username || url.password || url.pathname !== '/' || url.search || url.hash) throw new Error('验收地址无效。')
  return url.origin
}

/** 为一次目标调用构造不包含正文、凭据或供应商响应的完成证据。 */
function evidence(job: AcceptanceJob, text: string, passed: boolean, lifecycle: boolean, pageObserved: boolean): Evidence {
  return { passed, lifecycle, pageObserved, agentId: job.agentId, threadId: job.threadId, runId: job.runId,
    textSha256: createHash('sha256').update(text).digest('hex'), textBytes: Buffer.byteLength(text),
    reason: passed ? '' : '目标未交回完整、匹配身份的文本运行终态。' }
}

/** 使用官方客户端执行 Agent/Endpoint 的唯一一次固定提问，不重试。 */
async function endpointAcceptance(job: AcceptanceJob): Promise<Evidence> {
  const { HttpAgent } = await import('@ag-ui/client')
  const agent = new HttpAgent({ url: `${localUrl(job.runtimeUrl)}/agents/${job.agentId}/run`, threadId: job.threadId })
  agent.addMessage({ id: randomUUID(), role: 'user', content: job.question })
  agent.setState({})
  let custom: unknown; let snapshot: unknown; let terminal: unknown
  let started = false; let finished = false; let forbidden = false
  const subscriber: AgentSubscriber = {
    onRunStartedEvent: ({ event }) => { started = event.threadId === job.threadId && event.runId === job.runId },
    onCustomEvent: ({ event }) => { if (event.name === 'agent_runtime_result.v1') custom = event.value; else forbidden = true },
    onStateChanged: ({ state }) => { snapshot = (state as { agentRuntime?: unknown }).agentRuntime },
    onRunFinishedEvent: ({ event }) => { finished = event.threadId === job.threadId && event.runId === job.runId; terminal = event.result },
    onToolCallStartEvent: () => { forbidden = true },
    onRunErrorEvent: () => { forbidden = true }
  }
  const timeout = setTimeout(() => agent.abortRun(), 120000)
  try {
    await agent.runAgent({ runId: job.runId }, subscriber)
    const payload = custom as { agentId?: string; threadId?: string; runId?: string; status?: string; pendingInteraction?: unknown } | undefined
    const result = terminal as { agentRuntime?: unknown } | undefined
    const lifecycle = started && finished && !forbidden && Boolean(payload)
      && payload?.agentId === job.agentId && payload.threadId === job.threadId && payload.runId === job.runId
      && payload.status === 'completed' && !payload.pendingInteraction
      && JSON.stringify(custom) === JSON.stringify(snapshot) && JSON.stringify(custom) === JSON.stringify(result?.agentRuntime)
    const text = agent.messages.filter((message) => message.role === 'assistant' && typeof message.content === 'string')
      .map((message) => message.content).join('')
    return evidence(job, text, lifecycle && Boolean(text.trim()) && Buffer.byteLength(text) <= 131072, lifecycle, false)
  } finally { clearTimeout(timeout) }
}

/** 在真实 Electron 中操作实际页面输入区，页面 SDK 的同一次调用同时提供协议与渲染证据。 */
async function pageAcceptance(job: AcceptanceJob): Promise<Evidence> {
  const origin = localUrl(job.pageUrl)
  const window = new BrowserWindow({ show: false, width: 1280, height: 900,
    webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false,
      partition: `runtime-acceptance-${randomUUID()}`, backgroundThrottling: false } })
  window.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))
  const timeout = setTimeout(() => { if (!window.isDestroyed()) window.destroy() }, 123000)
  window.webContents.on('will-navigate', (event, url) => { if (new URL(url).origin !== origin) event.preventDefault() })
  try {
    await window.loadURL(origin)
    const result = await window.webContents.executeJavaScript(`new Promise((resolve) => {
      const expected = ${JSON.stringify({ agentId: job.agentId, threadId: job.threadId, runId: job.runId })};
      let sent = false;
      const timer = setTimeout(() => finish({ passed: false, text: '', lifecycle: false }), 120000);
      const finish = (value) => { clearTimeout(timer); clearInterval(ready); window.removeEventListener('das-runtime-acceptance', receive); resolve(value); };
      const receive = (event) => {
        const value = event.detail;
        if (!sent || value.agentId !== expected.agentId || value.threadId !== expected.threadId || value.runId !== expected.runId) return;
        requestAnimationFrame(() => requestAnimationFrame(() => {
          const root = document.querySelector('[data-agent-id="' + expected.agentId + '"]');
          const visible = [...(root?.querySelectorAll('.x-agent-message:not(.x-agent-message--user) .x-agent-message__bubble') || [])]
            .some((element) => element.getBoundingClientRect().height > 0 && element.textContent?.includes(value.text));
          finish({ passed: value.lifecycle && visible && Boolean(value.text?.trim()), text: value.text || '', lifecycle: value.lifecycle });
        }));
      };
      window.addEventListener('das-runtime-acceptance', receive);
      const ready = setInterval(() => {
        if (sent) return;
        const root = document.querySelector('[data-agent-id="' + expected.agentId + '"]');
        const launcher = root?.querySelector('.x-agent-floating__launcher');
        if (launcher && !root.querySelector('textarea')) launcher.click();
        const input = root?.querySelector('textarea');
        const button = [...(root?.querySelectorAll('.x-agent-composer button') || [])].find((item) => item.textContent?.trim() === '发送');
        if (!input || input.disabled || !button) return;
        if (input.value !== ${JSON.stringify(job.question)}) {
          Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(input, ${JSON.stringify(job.question)});
          input.dispatchEvent(new Event('input', { bubbles: true }));
          return;
        }
        if (button.disabled) return;
        sent = true; clearInterval(ready); button.click();
      }, 100);
    })`) as { passed: boolean; text: string; lifecycle: boolean }
    return evidence(job, result.text, result.passed, result.lifecycle, result.passed)
  } finally { clearTimeout(timeout); if (!window.isDestroyed()) window.destroy() }
}

/** 沿 Studio 现有 AG-UI 动作信封传递任务，解析由官方 SDK 完成。 */
async function bridgeAction(baseUrl: string, action: Record<string, unknown>): Promise<Record<string, unknown>> {
  const { HttpAgent } = await import('@ag-ui/client')
  const agent = new HttpAgent({ url: `${baseUrl.replace(/\/$/, '')}/agent-runtime-acceptance/run`, threadId: randomUUID() })
  const timeout = setTimeout(() => agent.abortRun(), 25000)
  try {
    const response = await agent.runAgent({ forwardedProps: { runtimeAcceptance: action } })
    const result = (response.result as { runtimeAcceptance?: Record<string, unknown> } | undefined)?.runtimeAcceptance
    if (result?.status !== 'completed') throw new Error('桌面验收桥未完成。')
    return result
  } finally { clearTimeout(timeout) }
}

/** 后端就绪后持续领取新任务；已领取调用永不重入队，退出立即停止领取。 */
export function startRuntimeAcceptanceWorker(baseUrl: string): void {
  let stopped = false
  app.once('before-quit', () => { stopped = true })
  /** 单消费者串行执行签发任务，供应商失败只交回失败事实。 */
  async function consume(): Promise<void> {
    let connectionFailures = 0
    while (!stopped) {
      let response: Record<string, unknown>
      try {
        response = await bridgeAction(baseUrl, { action: 'claim', verifierSha256: createHash('sha256').update(verifierSource).digest('hex') })
        connectionFailures = 0
      } catch {
        if (stopped) break
        // 这里只恢复未领取任务的控制连接；后端重启或启动瞬断不能永久关闭消费者。
        // 已领取的 job 不进入此分支，绝不因重连重复提交模型调用。
        connectionFailures += 1
        await new Promise<void>((resolve) => setTimeout(resolve, Math.min(connectionFailures, 5) * 1000))
        continue
      }
      const job = response.job as AcceptanceJob | null
      if (!job || stopped) continue
      let result = evidence(job, '', false, false, false)
      try {
        if (!/^[a-z][a-z0-9_]{0,127}$/.test(job.agentId)) throw new Error('验收身份无效。')
        result = job.mode === 'page' ? await pageAcceptance(job) : await endpointAcceptance(job)
      } catch { /* 不记录含供应商响应、页面正文或连接凭据的原始异常。 */ }
      try {
        await bridgeAction(baseUrl, { action: 'complete', token: job.token, evidence: result })
      } catch {
        // 交回失败只保留结果未知，继续接收新签发任务，不重新执行已领取任务。
        console.error('桌面验收结果未确认送达；本次任务不会重放。')
      }
    }
  }
  void consume().catch(() => { console.error('桌面目标验收桥已停止；未完成任务不会自动重放。') })
}
