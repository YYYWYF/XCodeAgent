import fs from 'node:fs/promises'
import path from 'node:path'

export const PRODUCT_PLAN_SCHEMA_VERSION = 'product-plan.v6'

/** 把 endpoint 业务标识转换为与规划产物约定一致的安全文件名。 */
function endpointDocumentStem(apiContractId: string, endpointId: string): string {
  const normalized = `${apiContractId}--${endpointId}`
    .replace(/[^a-zA-Z0-9_-]+/g, '-')
    .replace(/^[-_]+|[-_]+$/g, '')
  return `endpoint--${normalized || 'unknown'}`
}

/** 返回 endpoint 用户可读设计文档的当前约定路径。 */
export function endpointDesignDocumentPath(
  workspaceRoot: string,
  apiContractId: string,
  endpointId: string
): string {
  return path.join(
    workspaceRoot,
    '.xcodeagent',
    'plans',
    'endpoints',
    `${endpointDocumentStem(apiContractId, endpointId)}.md`
  )
}

/** 只按非空 endpoint Markdown 是否真实落盘判断接口是否已经完成设计。 */
export async function endpointDesignDocumentExists(
  workspaceRoot: string,
  apiContractId: string,
  endpointId: string
): Promise<boolean> {
  try {
    const content = await fs.readFile(
      endpointDesignDocumentPath(workspaceRoot, apiContractId, endpointId),
      'utf8'
    )
    return Boolean(content.trim())
  } catch {
    return false
  }
}
