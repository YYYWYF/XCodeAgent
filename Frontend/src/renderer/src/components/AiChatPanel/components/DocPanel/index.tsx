import { FileTextOutlined } from '@ant-design/icons'
import { Alert, Button, Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import MarkdownContent from '../../../MarkdownContent/MarkdownContent'
import RichLoading from '../DesignProgress/RichLoading'
import RequirementDocPanel from './RequirementDocPanel'
import ProductPlanDocPanel from './ProductPlanDocPanel'
import TechnicalPlanReviewPanel from './TechnicalPlanReviewPanel'
import './index.less'

const { Text } = Typography

/** 将 TechnicalPlan 的确认状态转换为顶部工具栏中的可读标签。 */
function technicalPlanStatusLabel(plan?: Record<string, unknown>): string {
  const status = plan?.confirmation_status
  return status === 'confirmed'
    ? '已确认'
    : status === 'pending_user_confirmation'
      ? '待确认'
      : '草稿'
}

/** 将需求或产品文档的确认状态转换为顶部工具栏中的可读标签。 */
function documentStatusLabel(spec?: Record<string, unknown>): string {
  const status = spec?.confirmation_status
  return status === 'confirmed'
    ? '已确认'
    : status === 'pending_user_confirmation'
      ? '待确认'
      : '草稿'
}

type Props = {
  content?: string
  title?: string
  generating?: boolean
  error?: string
  onRetry?: () => void
  docName?: string
  productPlan?: Record<string, unknown>
  requirementSpec?: Record<string, unknown>
  technicalPlan?: Record<string, unknown>
  technicalPlanBaseline?: Record<string, unknown>
  technicalPlanUpdating?: boolean
  technicalPlanReviewKey?: string
  technicalPlanStatus?: string
  structuredDocument?: 'requirement-doc' | 'product-plan' | 'technical-plan'
  structuredDocumentLoading?: boolean
  workspaceRoot?: string
  onReviseAuthorization?: () => void
}

/** 右侧产物面板：需求、产品与技术规划使用结构化视图，其余文档只读展示 Markdown。 */
export default function DocPanel({
  content,
  title,
  generating,
  error,
  onRetry,
  docName,
  productPlan,
  requirementSpec,
  technicalPlan,
  technicalPlanBaseline,
  technicalPlanUpdating,
  technicalPlanReviewKey,
  technicalPlanStatus,
  structuredDocument,
  structuredDocumentLoading,
  workspaceRoot,
  onReviseAuthorization
}: Props): ReactElement {
  const isTechnicalPlan = structuredDocument === 'technical-plan'
  const isRequirementDoc = structuredDocument === 'requirement-doc'
  const isProductPlan = structuredDocument === 'product-plan'
  const reviewDocument = isProductPlan ? productPlan : requirementSpec
  const requirementReady =
    isRequirementDoc && Boolean(requirementSpec && Object.keys(requirementSpec).length)
  const ready = Boolean(
    content ||
      technicalPlan ||
      isTechnicalPlan ||
      requirementReady ||
      (isProductPlan && productPlan) ||
      error ||
      structuredDocumentLoading
  )

  return (
    <div className={cx('doc-panel')}>
      <header className={cx('doc-panel-toolbar')}>
        {isTechnicalPlan ? (
          <div className={cx('doc-panel-path', 'doc-panel-technical-path')}>
            <FileTextOutlined aria-hidden="true" />
            <span>
              <strong>技术规划</strong>
            </span>
            {technicalPlan ? (
              <Tag className={cx('doc-panel-technical-status')}>
                {technicalPlanStatus || technicalPlanStatusLabel(technicalPlan)}
              </Tag>
            ) : null}
          </div>
        ) : isRequirementDoc || isProductPlan ? (
          <div className={cx('doc-panel-path', 'doc-panel-technical-path')}>
            <FileTextOutlined aria-hidden="true" />
            <span>
              <strong>{isProductPlan ? '产品规划' : '需求文档'}</strong>
              <small>
                {title?.split('/').pop() ||
                  (isProductPlan ? 'product-plan.json' : 'requirement-spec.json')}
              </small>
            </span>
            {reviewDocument ? (
              <Tag
                className={cx(
                  'doc-panel-technical-status',
                  reviewDocument.confirmation_status === 'confirmed' &&
                    'doc-panel-technical-status-confirmed'
                )}
              >
                {documentStatusLabel(reviewDocument)}
              </Tag>
            ) : null}
          </div>
        ) : (
          <div className={cx('doc-panel-path')}>{title || '文档'}</div>
        )}
      </header>
      <div className={cx('doc-panel-stage', ready && 'editor')}>
        {generating || structuredDocumentLoading ? (
          <div className={cx('doc-panel-generating')}>
            <RichLoading
              bare
              title={structuredDocumentLoading ? '正在读取文档…' : `正在生成${docName || '文档'}…`}
            />
          </div>
        ) : error ? (
          <div className={cx('doc-panel-empty', 'doc-panel-error')}>
            <Alert
              message="文档读取失败"
              description={error}
              showIcon
              type="error"
              action={onRetry ? <Button onClick={onRetry}>重新读取</Button> : undefined}
            />
          </div>
        ) : ready ? (
          <div
            className={cx(
              'doc-panel-viewer',
              isTechnicalPlan || requirementReady || isProductPlan
                ? 'doc-panel-structured'
                : 'doc-panel-markdown'
            )}
          >
            {isTechnicalPlan ? (
              technicalPlan && productPlan ? (
                <TechnicalPlanReviewPanel
                  key={technicalPlanReviewKey}
                  plan={technicalPlan}
                  productPlan={productPlan}
                  baseline={technicalPlanBaseline}
                  updating={technicalPlanUpdating}
                  onReviseAuthorization={onReviseAuthorization}
                />
              ) : (
                <div className={cx('doc-panel-empty')}>
                  <Text strong>技术规划可视化暂不可用</Text>
                  <Text type="secondary">未读取到需求或技术规划的结构化数据</Text>
                </div>
              )
            ) : requirementReady ? (
              <RequirementDocPanel
                productPlan={productPlan || {}}
                spec={requirementSpec || {}}
                workspaceRoot={workspaceRoot}
              />
            ) : isProductPlan && productPlan ? (
              <ProductPlanDocPanel productPlan={productPlan} />
            ) : (
              <MarkdownContent content={content ?? ''} />
            )}
          </div>
        ) : (
          <div className={cx('doc-panel-empty')}>
            <span className={cx('doc-panel-orb')}>
              <FileTextOutlined />
            </span>
            <Text strong>{docName ? `${docName}待生成` : '文档将在此生成'}</Text>
            <Text type="secondary">文档生成后会展示在这里</Text>
          </div>
        )}
      </div>
    </div>
  )
}
