import { app, net, protocol } from 'electron'
import { join } from 'node:path'
import { pathToFileURL } from 'node:url'
import { DESIGN_RUNTIME_SCHEME, resolveDesignRuntimeFile } from './designRuntimePath'

/** 在 Electron ready 前注册与主窗口隔离的设计稿资源协议。 */
export function registerDesignRuntimeScheme(): void {
  protocol.registerSchemesAsPrivileged([
    {
      scheme: DESIGN_RUNTIME_SCHEME,
      privileges: { standard: true, secure: true, supportFetchAPI: true }
    }
  ])
}

/** 只向设计稿 iframe 提供两份固定的本地资源，拒绝任意路径访问。 */
export function installDesignRuntimeProtocol(): void {
  const runtimeDirectory = app.isPackaged
    ? join(app.getAppPath(), 'out', 'renderer', 'design-runtime')
    : join(app.getAppPath(), 'src', 'renderer', 'public', 'design-runtime')

  protocol.handle(DESIGN_RUNTIME_SCHEME, (request) => {
    const assetPath = resolveDesignRuntimeFile(request.url, runtimeDirectory)
    if (!assetPath) {
      return new Response('Not found', { status: 404 })
    }
    return net.fetch(pathToFileURL(assetPath).href)
  })
}
