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
 * 读不到某个页面的源码就原样留空：按钮禁用总好过报错。manifest 结构不符时原样返回。
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
      if (!pageKey) return page
      try {
        const code = await fs.readFile(path.join(pagesDir, pageKey, 'index.tsx'), 'utf8')
        return { ...(page as Record<string, unknown>), code }
      } catch {
        return page
      }
    })
  )
  return { ...(manifest as Record<string, unknown>), pages: enriched }
}
