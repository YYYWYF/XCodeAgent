import { createServer } from 'node:http'
import { createMockAgentHttpHandler } from '@za38.03/agent-ui-creator/mock-agent'
import { resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

/** 独立测试进程只托管官方 Mock，保持消费者生产依赖中没有 Creator。 */
export function createConsumerMockServer(defaultScenarioId = 'simple-chat') {
  const handler = createMockAgentHttpHandler({ defaultScenarioId })
  return createServer((request, response) => {
    void handler(request, response).catch((error) => {
      console.error(error.message)
      if (!response.headersSent) response.writeHead(500)
      response.end()
    })
  })
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const server = createConsumerMockServer()
  server.on('error', (error) => { console.error(error.message); process.exitCode = 1 })
  server.listen(47835, '127.0.0.1', () => console.log('Official Mock: http://127.0.0.1:47835'))
  /** 测试结束时关闭本进程拥有的连接，不影响真实业务 Runtime。 */
  function close() {
    server.closeAllConnections()
    server.close()
  }
  process.once('SIGINT', close)
  process.once('SIGTERM', close)
}
