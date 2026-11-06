import { FileTextOutlined } from '@ant-design/icons'
import { Alert, Button, Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import MarkdownContent from '../../../MarkdownContent/MarkdownContent'
import RichLoading from '../DesignProgress/RichLoading'
import RequirementDocPanel from './RequirementDocPanel'
import TechnicalPlanDocPanel from './TechnicalPlanDocPanel'
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

/** 将 RequirementSpec 的确认状态转换为顶部工具栏中的可读标签。 */
function requirementStatusLabel(spec?: Record<string, unknown>): string {
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
  technicalPlanStatus?: string
  structuredDocument?: 'requirement-doc' | 'technical-plan'
  structuredDocumentLoading?: boolean
  workspaceRoot?: string
  onReviseAuthorization?: () => void
}

/** 右侧产物面板：需求文档与技术规划使用结构化审核视图，其余文档只读展示 Markdown。 */
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
  technicalPlanStatus,
  structuredDocument,
  structuredDocumentLoading,
  workspaceRoot,
  onReviseAuthorization
}: Props): ReactElement {
  const isTechnicalPlan = structuredDocument === 'technical-plan'
  const isRequirementDoc = structuredDocument === 'requirement-doc'
  const requirementReady =
    isRequirementDoc && Boolean(requirementSpec && Object.keys(requirementSpec).length)
  const ready = Boolean(
    content ||
      technicalPlan ||
      isTechnicalPlan ||
      requirementReady ||
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
              <small>{title?.split('/').pop() || 'technical-plan.json'}</small>
            </span>
            {technicalPlan ? (
              <Tag className={cx('doc-panel-technical-status')}>
                {technicalPlanStatus || technicalPlanStatusLabel(technicalPlan)}
              </Tag>
            ) : null}
          </div>
        ) : isRequirementDoc ? (
          <div className={cx('doc-panel-path', 'doc-panel-technical-path')}>
            <FileTextOutlined aria-hidden="true" />
            <span>
              <strong>需求文档</strong>
              <small>{title?.split('/').pop() || 'requirement-spec.json'}</small>
            </span>
            {requirementSpec ? (
              <Tag
                className={cx(
                  'doc-panel-technical-status',
                  requirementSpec.confirmation_status === 'confirmed' &&
                    'doc-panel-technical-status-confirmed'
                )}
              >
                {requirementStatusLabel(requirementSpec)}
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
              isTechnicalPlan || requirementReady ? 'doc-panel-structured' : 'doc-panel-markdown'
            )}
          >
            {isTechnicalPlan ? (
              technicalPlan && productPlan ? (
                <TechnicalPlanDocPanel
                  plan={technicalPlan}
                  productPlan={productPlan}
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
