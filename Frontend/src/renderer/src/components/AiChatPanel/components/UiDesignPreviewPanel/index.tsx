import { CheckOutlined, InboxOutlined } from '@ant-design/icons'
import { Typography } from 'antd'
import type { ReactElement } from 'react'
import { useEffect, useMemo } from 'react'
import DesignPagePreview from '../../../DesignRenderer/DesignPagePreview'
import { designPageGenerationLabel, hasDesignPreview, type DesignPreviewPage } from '../../../DesignRenderer/projectPreview'
import RichLoading from '../DesignProgress/RichLoading'
import { cx } from '../../../../utils'
import './index.less'
import AgentUiTransfer from '../../../AgentUiTransfer'

const { Text } = Typography

/** 该页是否处于后台生成池处理中（queued/generating），尚未产出设计稿。 */
function isPoolGenerating(status?: string): boolean {
  return status === 'queued' || status === 'generating'
}

type UiDesignPage = DesignPreviewPage & {
  pageId?: string
  name?: string
  path?: string
  code?: string
  status?: string
}

type Props = {
  /** 当前选中页 id。 */
  activePageId: string
  /** 当前选中页的 .tsx 源码（DesignRenderer 编译渲染）。 */
  code: string
  /** 设计稿生成中（workflow running）。 */
  generating?: boolean
  /** 当前正在执行动作（选模板/换一换）的 pageId 集合，这些页显示加载态。 */
  actingPageIds?: string[]
  /** 切换选中页。 */
  onPageChange: (pageId: string) => void
  /** 全部页面（含未生成）。 */
  pages: UiDesignPage[]
  workspaceRoot?: string
  onOpenPreview?: (pageId: string) => void
}

/** 右侧「UI设计稿」面板：左侧页面列表 + 右侧 DesignRenderer 预览。
 *  与中间区 UiDesignConfirmationPanel 联动：选中页同步显示设计稿。 */
export default function UiDesignPreviewPanel({
  activePageId,
  code,
  generating,
  actingPageIds,
  onPageChange,
  pages,
  workspaceRoot,
  onOpenPreview
}: Props): ReactElement {
  const actingSet = useMemo(() => new Set(actingPageIds || []), [actingPageIds])
  // 当前选中页对象 + 是否正在生成（本地 acting 或后台 queued/generating）。
  const activePage = useMemo(
    () => pages.find((p) => (p.pageId || '') === activePageId),
    [pages, activePageId]
  )
  const activePageGenerating = useMemo(
    () =>
      activePage
        ? actingSet.has(activePageId) || isPoolGenerating(activePage.status)
        : false,
    [activePage, activePageId, actingSet]
  )
  const activeProgress = activePage ? designPageGenerationLabel(activePage, actingSet.has(activePageId)) : undefined
  // 首次进入或选中页失效时，默认选第一页。
  useEffect(() => {
    if (pages.length === 0) return
    const exists = pages.some((p) => (p.pageId || '') === activePageId)
    if (!exists) {
      const first = pages[0]
      if (first?.pageId) onPageChange(first.pageId)
    }
  }, [activePageId, onPageChange, pages])

  return (
    <div className={cx('ui-design-preview-panel')}>
      {workspaceRoot && pages.some(page => page.design_asset) && (
        <div className={cx('ui-design-preview-stage-header')}>
          <AgentUiTransfer key={workspaceRoot} workspaceRoot={workspaceRoot} />
        </div>
      )}
      <aside className={cx('ui-design-preview-anchor')}>
        <nav className={cx('ui-design-preview-list')}>
          {pages.map((page, index) => {
            const pageId = page.pageId || `page-${index + 1}`
            const confirmed = hasDesignPreview(page) && page.status === 'confirmed'
            const active = activePageId === pageId
            const generatingPage = actingSet.has(pageId) || isPoolGenerating(page.status)
            const progress = designPageGenerationLabel(page, actingSet.has(pageId))
            return (
              <button
                className={cx(
                  'ui-design-preview-item',
                  confirmed && !generatingPage && 'is-confirmed',
                  generatingPage && 'is-generating',
                  active && 'is-active'
                )}
                key={pageId}
                onClick={() => onPageChange(pageId)}
                type="button"
              >
                <span className={cx('ui-design-preview-index')}>
                  {generatingPage ? (
                    <span className={cx('ui-design-preview-dots')} aria-label={progress}>
                      <span />
                      <span />
                      <span />
                    </span>
                  ) : confirmed ? <CheckOutlined /> : index + 1}
                </span>
                <span className={cx('ui-design-preview-label')}>
                  {page.name || pageId}
                  {progress ? ` · ${progress}` : ''}
                </span>
              </button>
            )
          })}
        </nav>
      </aside>
      <div className={cx('ui-design-preview-stage')}>
        <div className={cx('ui-design-preview-stage-body')}>
          {activePage && hasDesignPreview(activePage) ? (
            <DesignPagePreview page={activePage} workspaceRoot={workspaceRoot} onOpenPreview={onOpenPreview} />
          ) : activePageGenerating || (generating && !code && actingSet.size === 0) ? (
            <div className={cx('ui-design-preview-loading')}>
              <RichLoading bare title={activeProgress === '排队中' ? '排队中，等待上一项完成…'
                : activeProgress === '提交中' ? '正在提交生成任务…' : '正在生成设计稿…'} />
            </div>
          ) : activePage?.error ? (
            <div className={cx('ui-design-preview-empty')} role="alert">
              <Text strong>{activePage.status === 'cancelled' ? '已停止生成' : '设计稿生成失败'}</Text>
              <Text type="secondary">{activePage.error}</Text>
            </div>
          ) : (
            <div className={cx('ui-design-preview-empty')}>
              <InboxOutlined className={cx('ui-design-preview-empty-icon')} />
              <Text strong>本页尚未生成设计稿</Text>
              <Text type="secondary">在中间区点击「换一换」或「选模板」生成</Text>
            </div>
          )}
          {activePageGenerating && activePage && hasDesignPreview(activePage) && (
            <div className={cx('ui-design-preview-progress')} role="status">
              {activeProgress}，暂时展示上一版设计稿
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
