import fs from 'node:fs/promises'
import path from 'node:path'
import { WORKSPACE_ARTIFACT_DIR_NAME } from './branding'

/**
 * 给 UI 设计稿 manifest 的每页补回 `code`（读 `ui-design/pages/<page_key>/index.tsx`）。
 *
 * 正式 manifest 刻意只存 `code_path` 不存源码 —— 源码是运行时数据，不入库。但确认界面
 * 要靠 `code` 渲染预览、「查看设计稿」按钮也按它判可用性。不回填的话，**重新打开工作区后
 * 每一页都点不开**：workflow 快照里的页面同样没有 code，本轮新生成的和从上一轮继承来的
 * 一视同仁。
 *
 * 单页读取失败通过临时 codeReadError 返回，不影响其他页，也不写回正式清单。
 */
export async function attachUiDesignPageCode(
  workspaceRoot: string,
  manifest: unknown
): Promise<unknown> {
  if (!manifest || typeof manifest !== 'object') return manifest
  const pages = (manifest as { pages?: unknown }).pages
  if (!Array.isArray(pages)) return manifest

  const pagesDir = path.join(workspaceRoot, WORKSPACE_ARTIFACT_DIR_NAME, 'ui-design', 'pages')
  const enriched = await Promise.all(
    pages.map(async (page) => {
      if (!page || typeof page !== 'object') return page
      // 已经带 code 的条目（生成池刚写完）保持原样，不做多余的磁盘读取。
      if ((page as { code?: unknown }).code) return page
      const pageKey = String((page as { page_key?: unknown }).page_key || '').trim()
      const expectsCode = (page as { status?: unknown }).status === 'confirmed' ||
        Boolean((page as { code_path?: unknown }).code_path)
      if (!pageKey) return expectsCode
        ? { ...page, codeReadError: '设计稿源码定位信息缺失，请重新读取。' } : page
      try {
        const code = await fs.readFile(path.join(pagesDir, pageKey, 'index.tsx'), 'utf8')
        return code.trim() ? { ...(page as Record<string, unknown>), code }
          : { ...page, codeReadError: '设计稿源码为空，请检查文件后重新读取。' }
      } catch (error) {
        // 未生成页没有文件是正常空态；已声明生成成功的文件缺失属于可重试的读取故障。
        if ((error as NodeJS.ErrnoException).code === 'ENOENT' && !expectsCode) return page
        return { ...page, codeReadError: '设计稿源码无法读取，请检查文件后重新读取。' }
      }
    })
  )
  return { ...(manifest as Record<string, unknown>), pages: enriched }
}
