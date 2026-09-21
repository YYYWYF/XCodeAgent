import type { DataSourceOperation, DataSourceOperationSection } from '../../typings'
import type { OperationDraft } from './ExternalApiFormParts'
import { parseJsonSampleText } from './jsonStructure'

/** 编辑器中按需配置的模块顺序，保持选择器和编辑态摘要一致。 */
export const OPERATION_SECTIONS: DataSourceOperationSection[] = ['path', 'query', 'header', 'requestBody', 'responseBody']

/** 判断 JSON 草稿是否包含可保存的非空内容。 */
export function hasJsonDraftContent(text: string, structure: DataSourceOperation['requestStructure']): boolean {
  if (structure !== null) return true
  const parsed = parseJsonSampleText(text)
  return !parsed.error && parsed.value !== undefined && parsed.value !== null
}

/** 根据已保存接口内容推导需要展示的配置模块。 */
export function configuredSections(operation: DataSourceOperation): DataSourceOperationSection[] {
  const sections: DataSourceOperationSection[] = []
  if (operation.pathParameters.length) sections.push('path')
  if (operation.queryParameters.length) sections.push('query')
  if (operation.headers.length) sections.push('header')
  if (operation.requestStructure !== null || (operation.requestSample !== undefined && operation.requestSample !== null)) sections.push('requestBody')
  if (operation.responseStructure !== null || (operation.responseSample !== undefined && operation.responseSample !== null)) sections.push('responseBody')
  return sections
}

/** 返回配置模块的中文标题和编辑说明。 */
export function sectionMeta(section: DataSourceOperationSection): { title: string; description: string } {
  const metadata: Record<DataSourceOperationSection, { title: string; description: string }> = {
    path: { title: 'Path 参数', description: '配置路径中的占位符参数' },
    query: { title: 'Query 参数', description: '配置 URL 查询参数' },
    header: { title: '接口 Header', description: '配置普通请求头' },
    requestBody: { title: '请求体', description: '用于记录接口请求示例' },
    responseBody: { title: '响应体', description: '用于记录接口响应示例' },
  }
  return metadata[section]
}

/** 清空指定模块的草稿内容，删除配置时不影响其他模块。 */
export function clearOperationSection(operation: OperationDraft, section: DataSourceOperationSection): OperationDraft {
  if (section === 'path') return { ...operation, pathParameters: [] }
  if (section === 'query') return { ...operation, queryParameters: [] }
  if (section === 'header') return { ...operation, headers: [] }
  if (section === 'requestBody') return { ...operation, requestSample: undefined, requestSampleText: '', requestStructure: null, requestFieldDescriptionsDraft: {}, requestFieldTypesDraft: {} }
  return { ...operation, responseSample: undefined, responseSampleText: '', responseStructure: null, responseFieldDescriptionsDraft: {}, responseFieldTypesDraft: {} }
}

/** 校验已添加模块不能保持空内容，避免下次编辑无法恢复模块状态。 */
export function validateSelectedSections(operation: OperationDraft, sections: ReadonlySet<DataSourceOperationSection>): void {
  if (sections.has('path') && operation.pathParameters.length === 0) throw new Error('Path 参数已添加，请至少填写一条参数。')
  if (sections.has('query') && operation.queryParameters.length === 0) throw new Error('Query 参数已添加，请至少填写一条参数。')
  if (sections.has('header') && operation.headers.length === 0) throw new Error('接口 Header 已添加，请至少填写一条 Header。')
  if (sections.has('requestBody') && !hasJsonDraftContent(operation.requestSampleText, operation.requestStructure)) throw new Error('请求体已添加，请填写非空 JSON 样例。')
  if (sections.has('responseBody') && !hasJsonDraftContent(operation.responseSampleText, operation.responseStructure)) throw new Error('响应体已添加，请填写非空 JSON 样例。')
}

/** 生成参数子卡片摘要，避免在标题栏重复渲染完整表格。 */
export function parameterSummary(names: string[], count: number): string {
  const visibleNames = names.filter(Boolean).slice(0, 3)
  return visibleNames.length ? `已配置 ${count} 个参数 · ${visibleNames.join('、')}` : `已配置 ${count} 个参数`
}
