import { join } from 'node:path'

export const DESIGN_RUNTIME_SCHEME = 'devagentstudio-design'

const DESIGN_RUNTIME_FILES: Readonly<Record<string, string>> = {
  '/design-frame.html': 'design-frame.html',
  '/antd5-runtime.js': 'antd5-runtime.js'
}

/** 只解析专用协议下允许发布的两份设计稿资源，拒绝其他主机和路径。 */
export function resolveDesignRuntimeFile(requestUrl: string, runtimeDirectory: string): string | null {
  try {
    const url = new URL(requestUrl)
    const fileName = DESIGN_RUNTIME_FILES[url.pathname]
    if (url.protocol !== `${DESIGN_RUNTIME_SCHEME}:` || url.hostname !== 'runtime' || !fileName) {
      return null
    }
    return join(runtimeDirectory, fileName)
  } catch {
    return null
  }
}
