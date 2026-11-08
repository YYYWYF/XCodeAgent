import type { ReactElement } from 'react'
import { useEffect, useState } from 'react'
import { InboxOutlined } from '@ant-design/icons'
import { WORKSPACE_ARTIFACT_DIR_NAME } from '../../../../constants/branding'
import { useUiDesignPagesWithCode } from '../../../../hooks/useUiDesignPagesWithCode'
import { readWorkspaceFile, WorkspaceToolError } from '../../../../service/workspaceTools'
import { cx } from '../../../../utils'
import type { ArtifactDocumentKey } from '../ApplicationOutline/artifactDocuments'
import DocPanel from '../DocPanel'
import UiDesignPreviewPanel from '../UiDesignPreviewPanel'

type UiPage = { pageId?: string; name?: string; status?: string; code?: string }
type DocumentData = {
  requirementSpec?: Record<string, unknown>
  productPlan?: Record<string, unknown>
  technicalPlan?: Record<string, unknown>
  pages?: UiPage[]
}
type Props = {
  workspaceRoot: string
  documentKey: ArtifactDocumentKey
  catalogLoading?: boolean
  initialUiDesignPageId?: string
}

/** 沿用工作区文件工具读取当前正式 JSON，截断或格式错误必须可见，避免显示不完整文档。 */
async function readArtifact(
  workspaceRoot: string,
  relativePath: string
): Promise<Record<string, unknown>> {
  const path = `${WORKSPACE_ARTIFACT_DIR_NAME}/${relativePath}`
  const result = await readWorkspaceFile({
    workspace_root: workspaceRoot,
    path,
    max_lines: 5000,
    max_chars: 200000
  })
  if (result.truncated) throw new Error(`${path} 内容超出读取范围，无法完整展示。`)
  const value: unknown = JSON.parse(result.content)
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new Error(`${path} 文档格式无效。`)
  return value as Record<string, unknown>
}

/** 按当前选择读取正式产物；需求文档继续合并 RequirementSpec 与 ProductPlan 的展示。 */
export async function readDocument(
  workspaceRoot: string,
  key: ArtifactDocumentKey
): Promise<DocumentData> {
  if (key === 'product-plan') {
    return { productPlan: await readArtifact(workspaceRoot, 'plans/product-plan.json') }
  }
  if (key === 'ui-design') {
    try {
      const manifest = await readArtifact(workspaceRoot, 'specs/ui-designs.json')
      if (!Array.isArray(manifest.pages)) throw new Error('UI 设计稿缺少页面清单。')
      return { pages: manifest.pages }
    } catch (error) {
      // 尚未生成清单是正常空态；权限、网络、服务和内容格式故障仍展示读取失败。
      if (error instanceof WorkspaceToolError && error.status === 404) return { pages: [] }
      throw error
    }
  }
  const [document, productPlan] = await Promise.all([
    readArtifact(
      workspaceRoot,
      key === 'requirement-spec' ? 'specs/requirement-spec.json' : 'plans/technical-plan.json'
    ),
    readArtifact(workspaceRoot, 'plans/product-plan.json')
  ])
  return key === 'requirement-spec'
    ? { requirementSpec: document, productPlan }
    : { technicalPlan: document, productPlan }
}

/** 在应用产物内部只读展示上游正式文档；切换工作区、目录刷新和重试均重新读取当前文件。 */
export default function ArtifactDocument({
  workspaceRoot,
  documentKey,
  catalogLoading,
  initialUiDesignPageId
}: Props): ReactElement {
  const [attempt, setAttempt] = useState(0)
  const [loaded, setLoaded] = useState<{ identity: string; data?: DocumentData; error?: string }>()
  const [activePageId, setActivePageId] = useState(initialUiDesignPageId || '')
  const identity = `${workspaceRoot}:${documentKey}:${attempt}`
  const current = loaded?.identity === identity ? loaded : undefined
  const loading = !current || catalogLoading === true
  const pages = useUiDesignPagesWithCode(
    documentKey === 'ui-design' ? workspaceRoot : undefined,
    current?.data?.pages
  )
  const activePage = pages.find((page) => page.pageId === activePageId) || pages[0]
  // 从页面概览指定页面时，不能用其他页面的设计稿冒充该页设计。
  const requestedPageMissing = Boolean(
    activePageId &&
      current?.data?.pages &&
      !current.data.pages.some((page) => page.pageId === activePageId)
  )

  useEffect(() => {
    if (catalogLoading) return
    let cancelled = false
    setLoaded(undefined)
    void readDocument(workspaceRoot, documentKey).then(
      (data) => {
        if (!cancelled) setLoaded({ identity, data })
      },
      (error: unknown) => {
        if (!cancelled)
          setLoaded({ identity, error: error instanceof Error ? error.message : String(error) })
      }
    )
    return () => {
      cancelled = true
    }
  }, [workspaceRoot, documentKey, identity, catalogLoading])

  if (documentKey === 'ui-design' && !loading && !current?.error) {
    if (pages.length === 0 || requestedPageMissing) {
      return (
        <div className={cx('development-artifacts-placeholder')} role="status">
          <InboxOutlined aria-hidden="true" />
          <h3>暂无设计稿</h3>
          <p>当前应用尚未生成 UI 设计稿。</p>
        </div>
      )
    }
    return (
      <UiDesignPreviewPanel
        activePageId={activePage?.pageId || ''}
        code={activePage?.code || ''}
        pages={pages}
        onPageChange={setActivePageId}
        readOnly
      />
    )
  }
  return (
    <DocPanel
      docName={documentKey === 'ui-design' ? 'UI 设计稿' : undefined}
      title={documentKey === 'ui-design' ? 'UI 设计稿' : undefined}
      workspaceRoot={workspaceRoot}
      requirementSpec={current?.data?.requirementSpec}
      productPlan={current?.data?.productPlan}
      technicalPlan={current?.data?.technicalPlan}
      structuredDocument={
        documentKey === 'requirement-spec'
          ? 'requirement-doc'
          : documentKey === 'product-plan'
            ? 'product-plan'
            : documentKey === 'technical-plan'
              ? 'technical-plan'
              : undefined
      }
      structuredDocumentLoading={loading}
      error={current?.error}
      onRetry={() => setAttempt((value) => value + 1)}
    />
  )
}
