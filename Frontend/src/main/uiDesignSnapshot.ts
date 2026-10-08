import fs from 'node:fs/promises'
import path from 'node:path'

type UiDesignPage = Record<string, unknown>

/** 判断设计稿文件是否位于当前工作区的页面源码目录内。 */
function isInsidePagesDirectory(filePath: string, pagesDirectory: string): boolean {
  const relative = path.relative(pagesDirectory, filePath)
  return relative !== '' && relative !== '..' && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative)
}

/** 读取当前工作区设计稿清单，并在安全边界内补齐各页 TSX 源码。 */
export async function readWorkspaceUiDesigns(workspaceRoot: string): Promise<Record<string, unknown> | null> {
  const manifestPath = path.join(workspaceRoot, '.xcodeagent', 'specs', 'ui-designs.json')
  let manifest: Record<string, unknown>
  try {
    const parsed: unknown = JSON.parse(await fs.readFile(manifestPath, 'utf8'))
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
    manifest = parsed as Record<string, unknown>
  } catch {
    return null
  }
  if (!Array.isArray(manifest.pages)) return manifest

  const pagesDirectory = path.join(workspaceRoot, '.xcodeagent', 'ui-design', 'pages')
  const realPagesDirectory = await fs.realpath(pagesDirectory).catch(() => null)
  const pages = await Promise.all(manifest.pages.map(async (item: unknown): Promise<UiDesignPage> => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return {}
    const page = item as UiDesignPage
    const codePath = typeof page.code_path === 'string' ? page.code_path : ''
    if (!codePath) return { ...page }
    const resolved = path.resolve(workspaceRoot, codePath)
    if (!isInsidePagesDirectory(resolved, pagesDirectory) || path.extname(resolved) !== '.tsx') {
      return { ...page, code: '', code_error: '设计稿源码路径不在当前项目的页面目录内。' }
    }
    try {
      const realPath = await fs.realpath(resolved)
      if (!realPagesDirectory || !isInsidePagesDirectory(realPath, realPagesDirectory)) {
        return { ...page, code: '', code_error: '设计稿源码路径不在当前项目的页面目录内。' }
      }
      const info = await fs.stat(realPath)
      if (!info.isFile() || info.size > 1024 * 1024) {
        return { ...page, code: '', code_error: '设计稿源码不是可读取的 TSX 文件。' }
      }
      const code = await fs.readFile(realPath, 'utf8')
      return { ...page, code, code_error: code.trim() ? undefined : '设计稿源码文件为空。' }
    } catch {
      return { ...page, code: '', code_error: '设计稿源码文件暂时无法读取。' }
    }
  }))
  return { ...manifest, pages }
}
