import { readWorkspaceFile } from '../../service/workspaceTools'
import type {
  ReadWorkspaceFileRequest,
  ReadWorkspaceFileResult
} from '../../service/workspaceTools'
import { validateBranchName } from '../../service/repositoryBranch'

export type DeliveryFileName = 'create.sql' | 'alter.sql'
export type DeliveryFileState = { content: string; error?: string }
export const DELIVERY_PREVIEW_MAX_CHARS = 2_000_000

/** 保留真实分支层级，并按后端版本目录规则拒绝路径穿越及静默改名。 */
export function databaseDeliveryPaths(branchName: string): Record<DeliveryFileName, string> {
  const error = validateBranchName(branchName)
  if (
    error ||
    branchName.trim() !== branchName ||
    branchName.split('/').some((part) => ['.', '..', ''].includes(part))
  ) {
    throw new Error(error || '数据库交付版本名无效。')
  }
  return {
    'create.sql': `backend/db/${branchName}/create.sql`,
    'alter.sql': `backend/db/${branchName}/alter.sql`
  }
}

/** 分页读取当前文件；重读可能被截断的边界行，并核对各页来自同一文件快照。 */
export async function readDeliveryFile(
  workspaceRoot: string,
  path: string,
  read: (request: ReadWorkspaceFileRequest) => Promise<ReadWorkspaceFileResult> = readWorkspaceFile,
  isActive: () => boolean = () => true
): Promise<DeliveryFileState> {
  let content = '',
    start = 1,
    digest: string | undefined,
    total: number | undefined
  try {
    if (!workspaceRoot.trim()) throw new Error('请先打开当前应用工作区。')
    while (isActive()) {
      const page = await read({
        workspace_root: workspaceRoot,
        path,
        start_line: start,
        max_lines: 5000,
        max_chars: 200000
      })
      if (!isActive()) throw new Error('读取已取消。')
      if (
        typeof page.content !== 'string' ||
        typeof page.truncated !== 'boolean' ||
        page.start_line !== start ||
        !Number.isInteger(page.end_line) ||
        !Number.isInteger(page.total_lines) ||
        page.total_lines < 0 ||
        page.end_line < start - 1 ||
        page.end_line > page.total_lines ||
        (!page.truncated && page.end_line !== page.total_lines)
      ) {
        throw new Error('文件读取结果无效，无法确认完整内容。')
      }
      if (total !== undefined && (page.sha256 !== digest || page.total_lines !== total))
        throw new Error('读取期间文件发生变化，请重试。')
      digest = page.sha256
      total = page.total_lines
      if (content.length + page.content.length > DELIVERY_PREVIEW_MAX_CHARS)
        throw new Error('内容被截断：文件超过预览的 200 万字符上限。')
      if (!page.truncated) return { content: content + page.content }
      // 后端 end_line 可能指向不完整的行；仅拼接其之前的完整行，下一页从边界行重读。
      const completeLineCount = page.end_line - start
      if (!digest || completeLineCount <= 0)
        throw new Error('内容被截断：单行超过读取上限或缺少文件快照，无法完整预览。')
      const lines =
        page.content.match(
          // 与后端 Python splitlines 保持一致，SQL 中的控制分隔符也算行边界。
          // eslint-disable-next-line no-control-regex
          /[^\r\n\v\f\x1c-\x1e\x85\u2028\u2029]*(?:\r\n|[\r\n\v\f\x1c-\x1e\x85\u2028\u2029]|$)/g
        ) || []
      if (completeLineCount > lines.length) throw new Error('内容被截断：分页行号无效。')
      content += lines.slice(0, completeLineCount).join('')
      start = page.end_line
    }
    throw new Error('读取已取消。')
  } catch (error) {
    return { content, error: error instanceof Error ? error.message : '读取失败。' }
  }
}
