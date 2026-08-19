import { CheckOutlined, InboxOutlined } from '@ant-design/icons'
import { Typography } from 'antd'
import type { ReactElement } from 'react'
import { useEffect } from 'react'
import DesignRenderer from '../../../DesignRenderer/DesignRenderer'
import RichLoading from '../DesignProgress/RichLoading'
import { cx } from '../../../../utils'
import './index.less'

const { Text } = Typography

type UiDesignPage = {
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
  /** 当前正在执行单页动作（选模板/换一换）的 pageId，该页显示加载态。 */
  actionPageId?: string | null
  /** 切换选中页。 */
  onPageChange: (pageId: string) => void
  /** 全部页面（含未生成）。 */
  pages: UiDesignPage[]
}

/** 右侧「UI设计稿」面板：左侧页面列表 + 右侧 DesignRenderer 预览。
 *  与中间区 UiDesignConfirmationPanel 联动：选中页同步显示设计稿。 */
export default function UiDesignPreviewPanel({
  activePageId,
  code,
  generating,
  actionPageId,
  onPageChange,
  pages
}: Props): ReactElement {
  const activePageGenerating = Boolean(
    actionPageId && actionPageId !== 'adjust' && activePageId === actionPageId
  )
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
      <aside className={cx('ui-design-preview-anchor')}>
        <nav className={cx('ui-design-preview-list')}>
          {pages.map((page, index) => {
            const pageId = page.pageId || `page-${index + 1}`
            const confirmed = Boolean(page.code) && page.status === 'confirmed'
            const active = activePageId === pageId
            const generatingPage = actionPageId === pageId
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
                    <span className={cx('ui-design-preview-dots')} aria-label="正在生成设计稿">
                      <span />
                      <span />
                      <span />
                    </span>
                  ) : confirmed ? <CheckOutlined /> : index + 1}
                </span>
                <span className={cx('ui-design-preview-label')}>
                  {page.name || pageId}
                </span>
              </button>
            )
          })}
        </nav>
      </aside>
      <div className={cx('ui-design-preview-stage')}>
        <div className={cx('ui-design-preview-stage-body')}>
          {activePageGenerating || (generating && !code && !actionPageId) ? (
            <div className={cx('ui-design-preview-loading')}>
              <RichLoading bare title="正在生成设计稿…" />
            </div>
          ) : code ? (
            <DesignRenderer
              code={code}
              title={`设计稿-${pages.find((p) => (p.pageId || '') === activePageId)?.name || activePageId}`}
            />
          ) : (
            <div className={cx('ui-design-preview-empty')}>
              <InboxOutlined className={cx('ui-design-preview-empty-icon')} />
              <Text strong>本页尚未生成设计稿</Text>
              <Text type="secondary">在中间区点击「换一换」或「选模板」生成</Text>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
