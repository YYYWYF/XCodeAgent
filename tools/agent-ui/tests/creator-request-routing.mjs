import { CreatorAgentRuntime } from '../node_modules/@za38.03/agent-ui-creator/dist/agent/runner.js'
import { resolveReadPath } from '../node_modules/@za38.03/agent-ui-creator/dist/agent/project-read.js'
import { readFile } from 'node:fs/promises'

// 只在测试中调用安装包的真实分类器；固定模型响应，不访问供应商或运行设计工具。
let input = ''
for await (const chunk of process.stdin) input += chunk
const requests = JSON.parse(input)
if (process.argv[2]) {
  // 同时用 SDK 的实际文件权限边界确认临时上下文可读。
  const location = await resolveReadPath({ root: process.argv[2] }, '/.agent-ui/design-revision-context.json')
  JSON.parse(await readFile(location.target, 'utf8'))
}
let modelCalls = 0
let scenario = {}
globalThis.fetch = async (_request, init) => {
  // 模型目录查询同样隔离；只把携带消息的模型调用计入分类请求。
  const body = init?.body ? JSON.parse(init.body) : {}
  if (!Array.isArray(body.messages)) return Response.json({})
  modelCalls++
  const event = {
    id: 'fixture', object: 'chat.completion.chunk', created: 1, model: 'fixture',
    choices: [{ index: 0, delta: { role: 'assistant', content: JSON.stringify(scenario.response ?? { permission: 'domain_write' }) }, finish_reason: null }]
  }
  const finish = { ...event, choices: [{ index: 0, delta: {}, finish_reason: 'stop' }] }
  return new Response(`data: ${JSON.stringify(event)}\n\ndata: ${JSON.stringify(finish)}\n\ndata: [DONE]\n\n`, {
    headers: { 'content-type': 'text/event-stream' }
  })
}
const runtime = { mode: 'domain-write', environment: {
  CREATOR_MODEL_BASE_URL: 'http://fixture.invalid/v1', CREATOR_MODEL_API_KEY: 'fixture',
  CREATOR_MODEL_NAME: 'fixture', CREATOR_MODEL_MAX_RETRIES: '0'
} }
const results = []
for (const request of requests) {
  scenario = typeof request === 'string' ? {} : request
  const before = modelCalls
  try {
    const result = await CreatorAgentRuntime.prototype.classify.call(runtime,
      [{ role: 'user', content: scenario.request ?? request }], 'routing-regression',
      new AbortController().signal, scenario.directory)
    results.push({ readOnly: result.readOnly, modelCalls: modelCalls - before })
  } catch (error) {
    results.push({ error: error.code ?? error.message, modelCalls: modelCalls - before })
  }
}
process.stdout.write(JSON.stringify(results))
