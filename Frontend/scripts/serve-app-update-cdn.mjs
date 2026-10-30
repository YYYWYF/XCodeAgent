/* eslint @typescript-eslint/explicit-function-return-type: off -- Node ESM scripts cannot use TypeScript return annotations. */
import path from 'node:path'
import { prepareCdn, createCdnServer } from './app-update-cdn.mjs'

try {
  const directory = process.argv[2] || path.resolve(import.meta.dirname, '../dist')
  const prepared = await prepareCdn(directory)
  const server = createCdnServer(prepared)
  server.on('error', (error) => {
    console.error(`本地 CDN 启动失败：${error.message}`)
    process.exitCode = 1
  })
  server.listen(18081, '127.0.0.1', () => {
    console.log(`本地 CDN：http://127.0.0.1:18081/，版本 ${prepared.version}`)
    console.log(`发布文件：${[...prepared.files.keys()].join('、')}`)
  })
  /** 终端退出时关闭本地服务，不遗留监听端口。 */
  const stop = () => {
    server.close()
    server.closeAllConnections()
  }
  process.once('SIGINT', stop)
  process.once('SIGTERM', stop)
} catch (error) {
  console.error(`本地 CDN 配置错误：${error.message}`)
  process.exitCode = 1
}
