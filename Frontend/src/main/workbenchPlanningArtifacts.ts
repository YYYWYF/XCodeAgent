import fs from 'node:fs/promises'
import path from 'node:path'
import { WORKSPACE_ARTIFACT_DIR_NAME } from './branding'
import { PRODUCT_PLAN_SCHEMA_VERSION } from './planningArtifactStatus'
import type { PlanningCatalogIssue } from '../shared/planningCatalog'

/** 校验目录所需的当前数组和稳定标识，避免格式损坏被投影器悄悄转换为空目录。 */
function validCatalog(plan: Record<string, unknown>, source: 'productPlan' | 'technicalPlan'): boolean {
  /** 检查一组目录记录均为具有稳定标识的对象。 */
  const records = (value: unknown, id: string): value is Record<string, unknown>[] =>
    Array.isArray(value) && value.every((item) => item && typeof item === 'object' &&
      !Array.isArray(item) && typeof item[id] === 'string' && item[id].trim())
  if (!records(plan.pages, 'pageId')) return false
  if (source === 'productPlan') return true
  return records(plan.entities, 'id') && records(plan.api_contracts, 'id') &&
    plan.api_contracts.every((contract) => records(contract.endpoints, 'id') &&
      contract.endpoints.every((endpoint) => typeof endpoint.path === 'string' && endpoint.path.trim()))
}

/** 独立读取当前规划供目录浏览，确认状态只影响正式准入，不隐藏结构有效的待确认内容。 */
export async function readWorkbenchPlanningArtifacts(workspaceRoot: string): Promise<{
  missing: string[]
  invalid: string[]
  catalogIssues: PlanningCatalogIssue[]
  productPlan?: Record<string, unknown>
  technicalPlan?: Record<string, unknown>
}> {
  const result: {
    missing: string[]
    invalid: string[]
    catalogIssues: PlanningCatalogIssue[]
    productPlan?: Record<string, unknown>
    technicalPlan?: Record<string, unknown>
  } = { missing: [], invalid: [], catalogIssues: [] }
  const artifacts = [
    { relativePath: 'specs/requirement-spec.md', format: 'markdown' },
    { relativePath: 'specs/requirement-spec.json', format: 'json' },
    { relativePath: 'plans/product-plan.md', format: 'markdown' },
    {
      relativePath: 'plans/product-plan.json', format: 'json',
      contractField: 'schema_version', contractValue: PRODUCT_PLAN_SCHEMA_VERSION,
      target: 'productPlan'
    },
    {
      relativePath: 'plans/technical-plan.json', format: 'json',
      contractField: 'artifact_type', contractValue: 'technical-plan', target: 'technicalPlan'
    }
  ] as const

  for (const artifact of artifacts) {
    /** 将当前目录源的真实读取失败单独返回，待确认状态不属于读取失败。 */
    const reportIssue = (reason: PlanningCatalogIssue['reason']): void => {
      if ('target' in artifact) result.catalogIssues.push({
        source: artifact.target === 'productPlan' ? 'product' : 'technical', reason
      })
    }
    try {
      const content = await fs.readFile(
        path.join(workspaceRoot, WORKSPACE_ARTIFACT_DIR_NAME, artifact.relativePath), 'utf8'
      )
      if (!content.trim()) {
        result.invalid.push(artifact.relativePath)
        reportIssue('invalid')
        continue
      }
      if (artifact.format === 'markdown') continue
      const value: unknown = JSON.parse(content)
      if (!value || typeof value !== 'object' || Array.isArray(value)) {
        result.invalid.push(artifact.relativePath)
        reportIssue('invalid')
        continue
      }
      const plan = value as Record<string, unknown>
      if ('contractField' in artifact && plan[artifact.contractField] !== artifact.contractValue) {
        result.invalid.push(artifact.relativePath)
        reportIssue('invalid')
        continue
      }
      if ('target' in artifact && (!validCatalog(plan, artifact.target) ||
        !['confirmed', 'pending_user_confirmation'].includes(String(plan.confirmation_status)))) {
        result.invalid.push(artifact.relativePath)
        reportIssue('invalid')
        continue
      }
      // 保留待确认错误使整体 ready 继续为 false；只向浏览目录提供当前契约内容，不提交或确认草稿。
      if (plan.confirmation_status !== 'confirmed') result.invalid.push(artifact.relativePath)
      if ('target' in artifact && (
        plan.confirmation_status === 'confirmed' ||
        plan.confirmation_status === 'pending_user_confirmation'
      )) result[artifact.target] = plan
    } catch (error: unknown) {
      if ((error as NodeJS.ErrnoException)?.code === 'ENOENT') {
        result.missing.push(artifact.relativePath)
        reportIssue('missing')
      } else {
        result.invalid.push(artifact.relativePath)
        reportIssue(error instanceof SyntaxError ? 'invalid' : 'unreadable')
      }
    }
  }
  return result
}
