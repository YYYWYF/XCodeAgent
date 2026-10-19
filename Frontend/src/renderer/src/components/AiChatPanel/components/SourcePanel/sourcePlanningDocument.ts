import {
  readWorkspaceFile,
  type ReadWorkspaceFileRequest,
  type ReadWorkspaceFileResult
} from '../../../../service/workspaceTools'
import type { JsonRecord } from '../DocPanel/RequirementDocPanelData'

export type SourcePlanningDocumentKey = 'requirement-spec' | 'product-plan' | 'technical-plan'

export type SourcePlanningDocumentRequest = {
  workspaceRoot: string
  documentKey: SourcePlanningDocumentKey
  documentPath: string
  productPlanPath?: string
  revision?: string
}

type FileReader = (request: ReadWorkspaceFileRequest) => Promise<ReadWorkspaceFileResult>

export type SourcePlanningDocument =
  | { documentKey: 'requirement-spec'; spec: JsonRecord; productPlan: JsonRecord }
  | { documentKey: 'product-plan'; productPlan: JsonRecord }
  | { documentKey: 'technical-plan'; plan: JsonRecord; productPlan: JsonRecord }

export const SOURCE_DOCUMENT_LABELS: Record<SourcePlanningDocumentKey, string> = {
  'requirement-spec': '需求文档',
  'product-plan': '产品规划',
  'technical-plan': '技术规划'
}

/** 读取所选 Markdown 对应的内部 JSON，保持工作区、草稿位置与 Git 修订一致。 */
export async function readSourcePlanningDocument(
  request: SourcePlanningDocumentRequest,
  readFile: FileReader = readWorkspaceFile
): Promise<SourcePlanningDocument> {
  const label = SOURCE_DOCUMENT_LABELS[request.documentKey]
  /** 校验完整对象后才交给可视化组件，避免截断或非法 JSON 被误展示为空文档。 */
  const readDocument = async (markdownPath: string): Promise<JsonRecord> => {
    const path = markdownPath.replace(/\.md$/, '.json')
    const result = await readFile({
      workspace_root: request.workspaceRoot,
      path,
      revision: request.revision,
      max_lines: 5000,
      max_chars: 200000
    })
    if (result.truncated) throw new Error(`${path} 文件过大，无法完整读取${label}`)
    const parsed: unknown = JSON.parse(result.content)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error(`${path} 不是有效的结构化文档`)
    }
    return parsed as JsonRecord
  }

  // 产品规划可独立展示；技术规划需要同一修订的产品规划补齐页面绑定语义。
  if (request.documentKey === 'product-plan') {
    return { documentKey: 'product-plan', productPlan: await readDocument(request.documentPath) }
  }
  if (request.documentKey === 'technical-plan' && !request.productPlanPath) {
    throw new Error('未找到产品规划，无法完整展示技术规划的页面绑定')
  }
  const [primary, productPlan] = await Promise.all([
    readDocument(request.documentPath),
    request.productPlanPath ? readDocument(request.productPlanPath) : Promise.resolve({})
  ])
  return request.documentKey === 'requirement-spec'
    ? { documentKey: 'requirement-spec', spec: primary, productPlan }
    : { documentKey: 'technical-plan', plan: primary, productPlan }
}
