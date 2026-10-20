import { Alert, Spin, Typography } from 'antd'
import { useEffect, useState, type ReactElement } from 'react'
import { cx } from '../../../../utils'
import RequirementDocPanel from '../DocPanel/RequirementDocPanel'
import TechnicalPlanDocPanel from '../DocPanel/TechnicalPlanDocPanel'
import ProductPlanDocPanel from '../DocPanel/ProductPlanDocPanel'
import {
  readSourcePlanningDocument,
  SOURCE_DOCUMENT_LABELS,
  type SourcePlanningDocument,
  type SourcePlanningDocumentRequest
} from './sourcePlanningDocument'

type Props = SourcePlanningDocumentRequest

/** 应用文件复用设计与计划阶段视图，只读加载所选规划及其依赖。 */
export default function SourcePlanningDoc({
  workspaceRoot,
  documentKey,
  documentPath,
  productPlanPath,
  revision
}: Props): ReactElement {
  const [document, setDocument] = useState<SourcePlanningDocument>()
  const [error, setError] = useState('')
  const label = SOURCE_DOCUMENT_LABELS[documentKey]

  useEffect(() => {
    let cancelled = false
    setDocument(undefined)
    setError('')
    void readSourcePlanningDocument({
      workspaceRoot,
      documentKey,
      documentPath,
      productPlanPath,
      revision
    })
      .then((result) => {
        if (!cancelled) setDocument(result)
      })
      .catch((cause: unknown) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : '读取规划文档失败')
      })
    return () => {
      cancelled = true
    }
  }, [workspaceRoot, documentKey, documentPath, productPlanPath, revision])

  if (error) {
    return (
      <div className={cx('source-panel-viewer-empty')}>
        <Alert message={`${label}读取失败`} description={error} showIcon type="error" />
      </div>
    )
  }
  if (!document) {
    return (
      <div className={cx('source-panel-viewer-loading')}>
        <Spin size="small" />
        <Typography.Text type="secondary">正在读取{label}…</Typography.Text>
      </div>
    )
  }
  return (
    <div className={cx('source-panel-doc', 'source-panel-structured')}>
      {document.documentKey === 'requirement-spec' ? (
        <RequirementDocPanel productPlan={document.productPlan} spec={document.spec} />
      ) : document.documentKey === 'product-plan' ? (
        <ProductPlanDocPanel productPlan={document.productPlan} />
      ) : (
        <TechnicalPlanDocPanel plan={document.plan} productPlan={document.productPlan} />
      )}
    </div>
  )
}
