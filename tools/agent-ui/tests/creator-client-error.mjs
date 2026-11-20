import { createServer } from 'node:http'
import { CreatorProcessManager, createCreatorClient } from '@za38.03/agent-ui-creator'

// 仅替代进程启动和服务端事件，使用安装包的真实公开客户端观察 RUN_ERROR 的传播。
const server = createServer(async (request, response) => {
  let body = ''
  for await (const chunk of request) body += chunk
  const input = JSON.parse(body)
  response.writeHead(200, { 'content-type': 'text/event-stream' })
  for (const event of [
    { type: 'RUN_STARTED', threadId: input.threadId, runId: input.runId },
    { type: 'STEP_STARTED', stepName: 'creator.resolve' },
    { type: 'RUN_ERROR', code: 'CREATOR_REQUEST_ROUTE_INVALID', message: 'CREATOR_REQUEST_ROUTE_INVALID' }
  ]) response.write(`data: ${JSON.stringify(event)}\n\n`)
  response.end()
})
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
CreatorProcessManager.prototype.ensureStarted = async () => ({
  host: '127.0.0.1', port: server.address().port, authToken: 'fixture'
})
CreatorProcessManager.prototype.dispose = async () => {}
const client = createCreatorClient({ projectRoot: process.cwd(), allowExternalEndpoint: false })
try {
  await client.run('实施共享 Agent UI 产品同步')
  throw new Error('Expected Creator to reject RUN_ERROR')
} catch (error) {
  process.stdout.write(JSON.stringify({ message: error.message, code: error.code ?? null }))
} finally {
  await client.dispose()
  server.closeAllConnections()
  await new Promise(resolve => server.close(resolve))
}
