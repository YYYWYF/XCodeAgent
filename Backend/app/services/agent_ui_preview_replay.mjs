import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFile, realpath } from 'node:fs/promises'
import { createHash } from 'node:crypto'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

/** 只解析受检成品的公开 export；AG-UI 编码器复用当前客户端已有依赖。 */
export async function loadPreviewMockApi(options) {
  const modules = await realpath(path.join(options.installationRoot, 'node_modules'))
  const root = await realpath(path.join(modules, '@za38.03/agent-ui-creator'))
  assert.ok(root.startsWith(modules + path.sep))
  const bytes = await readFile(path.join(root, 'package.json'))
  const metadata = JSON.parse(bytes)
  assert.equal(metadata.name, '@za38.03/agent-ui-creator')
  if (options.creatorVersion) assert.equal(metadata.version, options.creatorVersion)
  if (options.packageJsonSha256) assert.equal(createHash('sha256').update(bytes).digest('hex'), options.packageJsonSha256)
  const entry = await realpath(path.resolve(root, metadata.exports['./mock-agent'].import))
  assert.ok(entry.startsWith(root + path.sep))
  const api = await import(pathToFileURL(entry).href)
  const require = createRequire(path.join(root, 'package.json'))
  const clientRequire = createRequire(require.resolve('@ag-ui/client/package.json'))
  return { ...api, ...require('@ag-ui/core/schemas'), ...require('@ag-ui/client'), ...clientRequire('@ag-ui/encoder') }
}

/** 使用官方解析和验证后只重建显示所需白名单字段，不留正文、参数、结果或自定义载荷。 */
export function sanitizeRecording(api, text) {
  assert.equal(typeof text, 'string')
  assert.ok(Buffer.byteLength(text) <= 1024 * 1024)
  const recording = api.parseMockRecording(text, { id: 'preview-replay' })
  const events = recording.events
  assert.ok(events.length <= 2000 && events[0]?.event.type === 'RUN_STARTED')
  assert.ok(events.at(-1)?.atMs <= 120000, '回放最多两分钟')
  assert.ok(['RUN_FINISHED', 'RUN_ERROR'].includes(events.at(-1)?.event.type))
  const start = events[0].event
  assert.equal(events.filter(({ event }) => event.type === 'RUN_STARTED').length, 1)
  assert.equal(events.filter(({ event }) => ['RUN_FINISHED', 'RUN_ERROR'].includes(event.type)).length, 1)
  const ids = new Map()
  /** 对任意录制身份分配本录制内的无内容别名。 */
  const alias = value => {
    assert.equal(typeof value, 'string')
    if (!ids.has(value)) ids.set(value, `recording-${ids.size + 1}`)
    return ids.get(value)
  }
  const seenText = new Set()
  const safe = events.map(({ event, atMs }) => {
    assert.ok(event.threadId === undefined || event.threadId === start.threadId)
    assert.ok(event.runId === undefined || event.runId === start.runId)
    const next = { type: event.type }
    for (const name of ['threadId', 'runId', 'messageId', 'toolCallId', 'parentMessageId']) {
      if (event[name] !== undefined) next[name] = alias(event[name])
    }
    switch (event.type) {
      case 'RUN_STARTED': case 'RUN_FINISHED': case 'TEXT_MESSAGE_END': case 'TOOL_CALL_END': break
      case 'RUN_ERROR': next.message = '录制的错误（原文已隐藏）'; break
      case 'TEXT_MESSAGE_START': next.role = 'assistant'; break
      case 'TEXT_MESSAGE_CONTENT':
        next.delta = seenText.has(event.messageId) ? '' : '［录制正文已隐藏］'
        seenText.add(event.messageId)
        break
      case 'TOOL_CALL_START': next.toolCallName = 'recorded_tool'; break
      case 'TOOL_CALL_ARGS': next.delta = ''; break
      case 'TOOL_CALL_RESULT': next.content = '［工具结果已隐藏］'; next.role = 'tool'; break
      default: throw new Error('录制含不支持安全回放的事件；仅接受文本、工具和运行生命周期。')
    }
    return { atMs, event: next }
  })
  const output = safe.map(value => JSON.stringify(value)).join('\n')
  api.parseMockRecording(output, { id: 'preview-replay' })
  return { text: output, eventCount: safe.length, sha256: createHash('sha256').update(output).digest('hex') }
}

/** 官方 runner 负责身份重绑定与计时，官方 encoder 承载同一规范 AG-UI 连接。 */
export async function createReplayHandler(api, text) {
  const recording = api.parseMockRecording(text, { id: 'preview-replay' })
  /** 仅接受有界规范请求；浏览器断开会取消本次回放，不产生业务或模型调用。 */
  return async (request, response) => {
    const controller = new AbortController()
    const abort = () => controller.abort()
    response.once('close', abort)
    try {
      if (request.method !== 'POST') { response.writeHead(405); response.end(); return }
      const chunks = []
      let size = 0
      for await (const chunk of request) {
        size += chunk.length
        if (size > 1024 * 1024) throw new Error('request too large')
        chunks.push(chunk)
      }
      const input = api.RunAgentInputSchema.parse(JSON.parse(Buffer.concat(chunks).toString('utf8')))
      assert.ok(!input.tools.length && !input.resume, '回放不执行前端工具或恢复待办')
      const encoder = new api.EventEncoder({ accept: 'text/event-stream' })
      response.writeHead(200, { 'Content-Type': encoder.getContentType(), 'Cache-Control': 'no-store' })
      for await (const event of api.runMockRecording(input, recording, { signal: controller.signal, timingScale: 1 })) {
        if (controller.signal.aborted) break
        response.write(encoder.encode(event))
      }
      response.end()
    } catch {
      if (!response.headersSent) response.writeHead(400)
      response.end()
    } finally { response.off('close', abort) }
  }
}

/** 导入命令只把脱敏结果写回管道，原始内容不进入命令行、日志或持久化。 */
async function sanitizeStdin() {
  const chunks = []
  let size = 0
  for await (const chunk of process.stdin) {
    size += chunk.length
    assert.ok(size <= 2 * 1024 * 1024)
    chunks.push(chunk)
  }
  const options = JSON.parse(Buffer.concat(chunks).toString('utf8'))
  const api = await loadPreviewMockApi(options)
  process.stdout.write(JSON.stringify(sanitizeRecording(api, options.text)))
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  sanitizeStdin().catch(() => { process.stderr.write('录制校验失败：请使用单次完整的文本或工具 JSONL。\n'); process.exitCode = 1 })
}
